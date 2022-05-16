#   General utils
import logging

from detectron2.config import get_cfg
from detectron2.data import MetadataCatalog
from detectron2.engine import DefaultPredictor
from ditod import add_vit_config
from torch import cuda


def initializeModel(
    config_path,
    weights_path,
    threshold=0.75,
    label_map=["text", "title", "list", "table", "figure"],
):
    """Gets predictor and metadata

    Args:
        config_path (str): path to model configuration file (.yaml)
        weights (str): path to pre-trained weights
        threshold (float): detection score threshold. Defaults to 0.75
        label_map (list): label map used by the model. Defaults to example label map

    Returns:
        predictor and metadata respectively
    """

    logging.info(
        f"[Utils] Initializing model with a default threshold of {threshold} and the following label map: {label_map}"
    )
    opts = ["MODEL.WEIGHTS", weights_path]

    # instantiate config
    cfg = get_cfg()
    add_vit_config(cfg)
    cfg.merge_from_file(config_path)

    # add model weights URL to config
    cfg.merge_from_list(opts)

    # set device
    device = "cuda" if cuda.is_available() else "cpu"
    cfg.MODEL.DEVICE = device

    # set score threshold
    cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = threshold

    # define model & classes
    predictor = DefaultPredictor(cfg)
    metadata = MetadataCatalog.get(cfg.DATASETS.TEST[0])
    metadata.set(thing_classes=label_map)

    return predictor, metadata

# BATCH INFERENCE

from pathlib import Path
from typing import Iterable, List, NamedTuple

import cv2
import detectron2.data.transforms as T
import torch
from detectron2.checkpoint import DetectionCheckpointer
from detectron2.config import CfgNode, get_cfg
from detectron2.modeling import build_model
from detectron2.structures import Instances
from numpy import ndarray
from torch.utils.data import DataLoader, Dataset


"""
    Batch predictor, mostly copy/ paste from Kasper Fromm Pedersen
    GitHub: https://github.com/fromm1990
    source: https://github.com/facebookresearch/detectron2/issues/282
"""

class Prediction(NamedTuple):
    x: float
    y: float
    width: float
    height: float
    score: float
    class_name: str


class ImageDataset(Dataset):

    def __init__(self, imagery):
        self.imagery = imagery

    def __getitem__(self, index) -> ndarray:
        return self.imagery[index]

    def __len__(self):
        return len(self.imagery)


class BatchPredictor:
    def __init__(self, cfg: CfgNode, classes: List[str], batch_size: int, workers: int):
        self.cfg = cfg.clone()  # cfg can be modified by model
        self.classes = classes
        self.batch_size = batch_size
        self.workers = workers
        self.model = build_model(self.cfg)
        self.model.eval()

        checkpointer = DetectionCheckpointer(self.model)
        checkpointer.load(cfg.MODEL.WEIGHTS)

        self.aug = T.ResizeShortestEdge(
            [cfg.INPUT.MIN_SIZE_TEST, cfg.INPUT.MIN_SIZE_TEST],
            cfg.INPUT.MAX_SIZE_TEST
        )

        self.input_format = cfg.INPUT.FORMAT
        assert self.input_format in ["RGB", "BGR"], self.input_format

    def __collate(self, batch):
        data = []
        for image in batch:
            # Apply pre-processing to image.
            if self.input_format == "RGB":
                # whether the model expects BGR inputs or RGB
                image = image[:, :, ::-1]
            height, width = image.shape[:2]

            image = self.aug.get_transform(image).apply_image(image)
            image = image.astype("float32").transpose(2, 0, 1)
            image = torch.as_tensor(image)
            data.append({"image": image, "height": height, "width": width})
        return data

    def __call__(self, imagery) -> Iterable[List[Prediction]]:
        """[summary]

        :param imagery: [description]
        :type imagery: List[ndarrays] # CV2 format
        :yield: Predictions for each image
        :rtype: [type]
        """
        dataset = ImageDataset(imagery)
        loader = DataLoader(
            dataset,
            self.batch_size,
            shuffle=False,
            num_workers=self.workers,
            collate_fn=self.__collate,
            pin_memory=True
        )
        #   TODO: here we might just take the output of the model because they convert it to something else
        with torch.no_grad():
            for batch in loader:
                results: List[Instances] = self.model(batch)
                yield from [result['instances'] for result in results]

    def __map_predictions(self, instances: Instances):
        instance_predictions = zip(
            instances.get('pred_boxes'),
            instances.get('scores'),
            instances.get('pred_classes')
        )

        predictions = []
        for box, score, class_index in instance_predictions:
            x1 = box[0].item()
            y1 = box[1].item()
            x2 = box[2].item()
            y2 = box[3].item()
            width = x2 - x1
            height = y2 - y1
            prediction = Prediction(
                x1, y1, width, height, score.item(), self.classes[class_index])
            predictions.append(prediction)
        return predictions

def runBatchPredictor(
    config_path,
    weights_path,
    images,
    threshold=0.75,
    label_map=["text", "title", "list", "table", "figure"],
    ):

    #   TODO: these should be somewhere else!
    opts = ["MODEL.WEIGHTS", weights_path]

    # instantiate config
    cfg = get_cfg()
    add_vit_config(cfg)
    cfg.merge_from_file(config_path)

    # add model weights URL to config
    cfg.merge_from_list(opts)

    # set device
    device = "cuda" if cuda.is_available() else "cpu"
    cfg.MODEL.DEVICE = device

    # set score threshold
    cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = threshold

    #   configuration & NOTE: you don't know where we need this in the batch inference
    metadata = MetadataCatalog.get(cfg.DATASETS.TEST[0])
    metadata.set(thing_classes=label_map)

    #   create predictor instance
    #   TODO: change classes to label_map everywhere
    predictor = BatchPredictor(cfg=cfg, classes=label_map, batch_size=1, workers=2)
    print(predictor)
    #   inference
    predictions = predictor(images)
    for pred in predictions:
        print("Prediction:", pred)

