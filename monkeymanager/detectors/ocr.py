import os

import cv2
import keras
import numpy as np
import pyautogui

os.environ["CUDA_VISIBLE_DEVICES"] = "-1"

ocr_model = keras.models.load_model(
    "monkeymanager/models/model_weights_and_optimizer.h5"
)


def custom_ocr(img, resolution=pyautogui.size()):
    h, w = img.shape[:2]

    white = np.array([255, 255, 255])
    black = np.array([0, 0, 0])

    img[np.all(img != white, axis=-1)] = black

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    thresh = cv2.threshold(gray, 60, 255, cv2.THRESH_BINARY)[1]
    cnts, _ = cv2.findContours(
        thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    chrImages = []

    for c in cnts:
        minX, minY = np.min(c[:, 0, 0]), np.min(c[:, 0, 1])
        maxX, maxY = np.max(c[:, 0, 0]), np.max(c[:, 0, 1])
        chrImg = img[minY:maxY, minX:maxX]

        height_cond = (
            25 * resolution[0] / 2560 <= chrImg.shape[0] <= 60 * resolution[0] / 2560
        )
        width_cond = (
            14 * resolution[1] / 1440 <= chrImg.shape[1] <= 40 * resolution[1] / 1440
        )

        if height_cond and width_cond:
            chrImg = cv2.resize(chrImg, (50, 50))
            chrImg = cv2.copyMakeBorder(
                chrImg, 5, 5, 5, 5, cv2.BORDER_CONSTANT, value=(0, 0, 0)
            )
            chrImg = (chrImg[:, :, 0] // 255).astype(np.uint8)
            chrImages.append([minX, chrImg])

    chrImages.sort(key=lambda item: item[0])
    filteredChrImages = []
    currentX = 0

    for entry in chrImages:
        if currentX + 50 >= entry[0]:
            currentX = entry[0]
            filteredChrImages.append(entry)

    if not filteredChrImages:
        return "-1"

    chrImages = np.array([item[1] for item in filteredChrImages])
    predictions = ocr_model.predict(chrImages, verbose=0)

    number = "".join(
        str(np.argmax(prediction)) if np.argmax(prediction) != 10 else "/"
        for prediction in predictions
    )

    return number
