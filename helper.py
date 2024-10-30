import copy
import datetime
import json
import math
import os
import random
import re
import signal
import sys
import time
from enum import Enum
from functools import reduce
from os.path import exists

import cv2
import keyboard
import numpy as np
import pyautogui
from ahk import AHK

ahk = AHK()

pyautogui.FAILSAFE = False


class PlaythroughResult(Enum):
    UNDEFINED = 0
    WIN = 1
    DEFEAT = 2


class ValidatedPlaythroughs(Enum):
    EXCLUDE_NON_VALIDATED = 0
    INCLUDE_ALL = 1
    EXCLUDE_VALIDATED = 2


def tupleToStr(tup):
    output = ""
    for item in tup:
        if len(output):
            output = output + ", " + str(item)
        else:
            output = str(item)
    return output


def cutImage(image, area):
    return np.array(image[area[1] : (area[3] + 1), area[0] : (area[2]) + 1])


def imageAreasEqual(imageA, imageB, area):
    return (cutImage(imageA, area) == cutImage(imageB, area)).all()


def subImgEqualImgArea(img, subImg, area):
    return (cutImage(img, area) == subImg).all()


def userHasMonkeyKnowledge(name):
    return (
        monkeyKnowledgeEnabled
        and "monkey_knowledge" in userConfig
        and name in userConfig["monkey_knowledge"]
        and userConfig["monkey_knowledge"][name]
    )


def adjustPrice(
    price, difficulty, gamemode, action=None, monkey=None, discountPercentage=None
):
    discount = (
        int(discountPercentage) / 100
        if discountPercentage and str(discountPercentage).isdigit()
        else 0
    )
    priceReduction = 0
    if gamemode == "impoppable":
        factor = 1.2
    elif difficulty == "easy":
        factor = 0.85
    elif difficulty == "medium":
        factor = 1
    elif difficulty == "hard":
        factor = 1.08
    additionalFactor = 1

    if gamemode != "chimps":
        if (
            monkey
            and monkey["type"] == "hero"
            and action
            and action["action"] == "place"
            and userHasMonkeyKnowledge("hero_favors")
        ):
            additionalFactor = 0.9
        if (
            monkey
            and monkey["type"] == "spike"
            and monkey["name"] == "spike0"
            and action
            and action["action"] == "place"
            and userHasMonkeyKnowledge("first_last_line_of_defense")
        ):
            priceReduction += 150

    return (
        round(price * (1 - discount) * factor * additionalFactor / 5) * 5
        - priceReduction
    )


def getMonkeySellValue(cost):
    return round(cost * 0.7)


def getResolutionString(resolution=pyautogui.size()):
    return str(resolution[0]) + "x" + str(resolution[1])


def parseBTD6InstructionFileName(filename):
    matches = re.search(
        r"^(?:(?:own_|unvalidated_|unsuccessful_)?monkeymanager/maps/)?(?P<map>\w+)#(?P<gamemode>\w+)#(?P<resolution>(?P<resolution_x>\d+)x(?P<resolution_y>\d+))(?:#(?P<comment>.+))?\.btd6$",
        filename,
    )
    if not matches:
        return None
    matches = matches.groupdict()
    matches["noMK"] = False
    matches["noLL"] = False
    matches["noLLwMK"] = False
    for m in re.finditer(
        r"(?P<noMK>noMK(?:#|$))?(?:(?P<singleType>[a-z]+)Only(?:#|$))?(?P<noLL>noLL(?:#|$))?(?P<noLLwMK>noLLwMK(?:#|$))?(?P<gB>gB(?:#|$))?",
        matches["comment"] if "comment" in matches and matches["comment"] else "",
    ):
        if m.group("noMK"):
            matches["noMK"] = True
        if m.group("noLL"):
            matches["noLL"] = True
        if m.group("noLLwMK"):
            matches["noLLwMK"] = True
        if m.group("gB"):
            matches["gB"] = True
    return matches


def getBTD6InstructionsFileNameByConfig(
    thisConfig, folder="monkeymanager/own_maps", resolution=getResolutionString()
):
    return (
        folder
        + "/"
        + thisConfig["map"]
        + "#"
        + thisConfig["gamemode"]
        + "#"
        + resolution
        + (
            "#" + thisConfig["comment"]
            if "comment" in thisConfig and thisConfig["comment"]
            else ""
        )
        + ".btd6"
    )


def writeBTD6InstructionsFile(
    thisConfig, folder="monkeymanager/own_maps", resolution=getResolutionString()
):
    filename = getBTD6InstructionsFileNameByConfig(thisConfig, folder, resolution)

    if not exists(folder):
        os.mkdir(folder)

    fp = open(filename, "w")

    for action in thisConfig["steps"]:
        if action["action"] == "place":
            fp.write(
                "place "
                + (thisConfig["hero"] if action["type"] == "hero" else action["type"])
                + " "
                + action["name"]
                + " at "
                + tupleToStr(action["pos"])
                + (
                    " with " + action["discount"] + "% discount"
                    if "discount" in action
                    else ""
                )
                + "\n"
            )
        elif action["action"] == "upgrade":
            fp.write(
                "upgrade "
                + action["name"]
                + " path "
                + str(action["path"])
                + (
                    " with " + action["discount"] + "% discount"
                    if "discount" in action
                    else ""
                )
                + "\n"
            )
        elif action["action"] == "retarget":
            fp.write(
                "retarget "
                + action["name"]
                + (" to " + tupleToStr(action["to"]) if "to" in action else "")
                + "\n"
            )
        elif action["action"] == "special":
            fp.write("special " + action["name"] + "\n")
        elif action["action"] == "sell":
            fp.write("sell " + action["name"] + "\n")
        elif action["action"] == "remove":
            cost = ""
            while True:
                print(
                    "enter cost of obstacle removal at "
                    + tupleToStr(action["pos"])
                    + " >"
                )
                cost = input()
                if len(cost) and cost.isdigit():
                    break
                else:
                    print("non integer provided!")
            fp.write(
                "remove obstacle at "
                + tupleToStr(action["pos"])
                + " for "
                + str(cost)
                + "\n"
            )
        elif action["action"] == "await_round":
            fp.write("round " + str(action["round"]) + "\n")

    fp.close()


def parseBTD6InstructionsFile(
    filename, targetResolution=pyautogui.size(), gamemode=None
):
    fileConfig = parseBTD6InstructionFileName(filename)
    if not fileConfig:
        return None

    sandboxMode = False
    mapname = fileConfig["map"]
    if mapname not in maps:
        print("unknown map: " + str(mapname))
        return None

    gamemode = gamemode if gamemode else fileConfig["gamemode"]
    if gamemode not in gamemodes and gamemode not in sandboxGamemodes:
        print("unknown gamemode: " + str(gamemode))
        return None

    sandboxMode = gamemode in sandboxGamemodes
    if not exists(filename):
        print("unknown file: " + str(filename))
        return None

    with open(filename, "r") as fp:
        rawInputFile = fp.read()

    if not targetResolution and fileConfig["resolution"] != getResolutionString():
        customPrint(
            "tried parsing playthrough for non native resolution with rescaling disabled!"
        )
        return None
    elif fileConfig["resolution"] != getResolutionString(targetResolution):
        rawInputFile = convertPositionsInString(
            rawInputFile,
            [int(x) for x in fileConfig["resolution"].split("x")],
            targetResolution,
        )

    configLines = rawInputFile.splitlines()
    monkeys = {}
    newMapConfig = setup_initial_map_config(
        mapname, gamemode, sandboxMode, filename, fileConfig
    )

    if gamemode in {"deflation", "half_cash", "impoppable", "chimps"} or sandboxMode:
        add_gamemode_deflation_step(newMapConfig)

    for line in configLines:
        matches = re.search(
            r"^(?P<action>place|upgrade|retarget|special|sell|remove|round|speed) ?(?P<type>[a-z_]+)? "
            r"(?P<name>\w+)(?: (?:(?:at|to) (?P<x>\d+), (?P<y>\d+))?(?:path (?P<path>[0-2]))?)?"
            r"(?: for (?P<price>\d+|\?\?\?))?(?: with (?P<discount>\d{1,2}|100)% discount)?$",
            line,
        )
        if not matches:
            continue

        newSteps = process_action(matches, filename, newMapConfig, gamemode, monkeys)
        if newSteps:
            newMapConfig["steps"].extend(newSteps)

    newMapConfig["monkeys"] = monkeys
    return newMapConfig


def setup_initial_map_config(mapname, gamemode, sandboxMode, filename, fileConfig):
    return {
        "category": maps[mapname]["category"],
        "map": mapname,
        "page": maps[mapname]["page"],
        "pos": maps[mapname]["pos"],
        "difficulty": (
            gamemodes[gamemode]["group"]
            if not sandboxMode
            else sandboxGamemodes[gamemode]["group"]
        ),
        "gamemode": gamemode,
        "steps": [],
        "extrainstructions": 0,
        "filename": filename,
    }


def add_gamemode_deflation_step(newMapConfig):
    newMapConfig["steps"].append(
        {
            "action": "click",
            "pos": imageAreas["click"]["gamemode_deflation_message_confirmation"],
            "cost": 0,
        }
    )
    newMapConfig["extrainstructions"] = 1


def process_action(matches, filename, newMapConfig, gamemode, monkeys):
    action = matches.group("action")
    if action == "place":
        return handle_place_action(matches, filename, newMapConfig, gamemode, monkeys)
    elif action == "upgrade":
        return handle_upgrade_action(matches, filename, monkeys, newMapConfig, gamemode)
    elif action == "retarget":
        return handle_retarget_action(matches, filename, monkeys)
    elif action == "special":
        return handle_special_action(matches, filename, monkeys)
    elif action == "sell":
        return handle_sell_action(matches, filename, monkeys)
    elif action == "remove":
        return handle_remove_action(matches)
    elif action == "round":
        return handle_round_action(matches)
    elif action == "speed":
        return handle_speed_action(matches)
    return []


def handle_place_action(matches, filename, newMapConfig, gamemode, monkeys):
    name = matches.group("name")
    if name in monkeys:
        print(f"{filename}: monkey {name} placed twice! skipping!")
        return []
    if matches.group("type") in towers["monkeys"]:
        newStep, monkeys = handle_place_monkey(matches, newMapConfig, gamemode, monkeys)
    elif matches.group("type") in towers["heros"]:
        newStep, newMapConfig = handle_place_hero(matches, newMapConfig, gamemode)
    else:
        print(
            f"{filename}: monkey/hero {name} has unknown type: {matches.group('type')}! skipping!"
        )
        return []
    return [newStep]


def handle_upgrade_action(matches, filename, monkeys, newMapConfig, gamemode):
    newSteps = []
    newStep, monkeys = handle_upgrade(
        matches, filename, monkeys, newMapConfig, gamemode
    )
    if newStep:
        newSteps.append(newStep)
        if upgradeRequiresConfirmation(
            monkeys[matches.group("name")], int(matches.group("path"))
        ):
            newSteps.append(
                {
                    "action": "click",
                    "name": matches.group("name"),
                    "pos": imageAreas["click"]["paragon_message_confirmation"],
                    "cost": 0,
                }
            )
    return newSteps


def handle_retarget_action(matches, filename, monkeys):
    newStep = handle_retarget(matches, filename, monkeys)
    return [newStep] if newStep else []


def handle_special_action(matches, filename, monkeys):
    newStep = handle_special(matches, filename, monkeys)
    return [newStep] if newStep else []


def handle_sell_action(matches, filename, monkeys):
    newStep = handle_sell(matches, filename, monkeys)
    return [newStep] if newStep else []


def handle_remove_action(matches):
    newStep = handle_remove(matches)
    return [newStep] if newStep else []


def handle_round_action(matches):
    newStep = handle_round(matches)
    return [newStep] if newStep else []


def handle_speed_action(matches):
    newStep = handle_speed(matches)
    return [newStep] if newStep else []


def handle_place_monkey(matches, newMapConfig, gamemode, monkeys):
    name = matches.group("name")
    monkey_type = matches.group("type")
    newStep = {
        "action": "place",
        "type": monkey_type,
        "name": name,
        "key": keybinds["monkeys"][monkey_type],
        "pos": (int(matches.group("x")), int(matches.group("y"))),
        "cost": adjustPrice(
            towers["monkeys"][monkey_type]["base"],
            newMapConfig["difficulty"],
            gamemode,
            {"action": "place"},
            {
                "type": monkey_type,
                "name": name,
                "upgrades": [0, 0, 0],
            },
            matches.group("discount"),
        ),
    }
    if matches.group("discount"):
        newStep["discount"] = matches.group("discount")
    monkeys[name] = {
        "type": monkey_type,
        "name": name,
        "upgrades": [0, 0, 0],
        "pos": (int(matches.group("x")), int(matches.group("y"))),
        "value": newStep["cost"],
    }
    return newStep, monkeys


def handle_place_hero(matches, newMapConfig, gamemode):
    name = matches.group("name")
    hero_type = matches.group("type")
    newStep = {
        "action": "place",
        "type": "hero",
        "name": name,
        "key": keybinds["monkeys"]["hero"],
        "pos": (int(matches.group("x")), int(matches.group("y"))),
        "cost": adjustPrice(
            towers["heros"][hero_type]["base"],
            newMapConfig["difficulty"],
            gamemode,
            {"action": "place"},
            {
                "type": "hero",
                "name": name,
                "upgrades": [0, 0, 0],
            },
            matches.group("discount"),
        ),
    }
    if matches.group("discount"):
        newStep["discount"] = matches.group("discount")
    newMapConfig["hero"] = hero_type
    return newStep, newMapConfig


def handle_upgrade(matches, filename, monkeys, newMapConfig, gamemode):
    name = matches.group("name")
    if name not in monkeys:
        print(f"{filename}: monkey {name} unplaced! skipping!")
        return None, monkeys
    if monkeys[name]["type"] == "hero":
        print(f"{filename}: tried to upgrade hero {name}! skipping instruction!")
        return None, monkeys

    monkeyUpgrades = monkeys[name]["upgrades"]
    path = int(matches.group("path"))
    monkeyUpgrades[path] += 1
    if (
        sum(x > 2 for x in monkeyUpgrades) > 1
        or sum(x > 0 for x in monkeyUpgrades) > 2
        or monkeyUpgrades[path] > 5
    ):
        print(f"{filename}: invalid upgrade path for monkey {name}! skipping!")
        monkeyUpgrades[path] -= 1
        return None, monkeys

    cost = adjustPrice(
        towers["monkeys"][monkeys[name]["type"]]["upgrades"][path][
            monkeyUpgrades[path] - 1
        ],
        newMapConfig["difficulty"],
        gamemode,
        {"action": "upgrade", "path": path},
        monkeys[name],
        matches.group("discount"),
    )
    newStep = {
        "action": "upgrade",
        "name": name,
        "key": keybinds["path"][str(path)],
        "pos": monkeys[name]["pos"],
        "path": path,
        "cost": cost,
    }
    if matches.group("discount"):
        newStep["discount"] = matches.group("discount")

    monkeys[name]["value"] += cost
    return newStep, monkeys


def handle_retarget(matches, filename, monkeys):
    name = matches.group("name")
    if name not in monkeys:
        print(f"{filename}: monkey {name} unplaced! skipping!")
        return None

    newStep = {
        "action": "retarget",
        "name": name,
        "key": keybinds["others"]["retarget"],
        "pos": monkeys[name]["pos"],
        "cost": 0,
    }
    if matches.group("x"):
        newStep["to"] = (int(matches.group("x")), int(matches.group("y")))
    elif monkeys[name]["type"] == "mortar":
        print("mortar can only be retargeted to a position! skipping!")
        return None
    return newStep


def handle_special(matches, filename, monkeys):
    name = matches.group("name")
    if name not in monkeys:
        print(f"{filename}: monkey {name} unplaced! skipping!")
        return None
    return {
        "action": "special",
        "name": name,
        "key": keybinds["others"]["special"],
        "pos": monkeys[name]["pos"],
        "cost": 0,
    }


def handle_sell(matches, filename, monkeys):
    name = matches.group("name")
    if name not in monkeys:
        print(f"{filename}: monkey {name} unplaced! skipping!")
        return None
    return {
        "action": "sell",
        "name": name,
        "key": keybinds["others"]["sell"],
        "pos": monkeys[name]["pos"],
        "cost": -getMonkeySellValue(monkeys[name]["value"]),
    }


def handle_remove(matches):
    if matches.group("price") == "???":
        print("remove obstacle without price specified!")
        return None
    return {
        "action": "remove",
        "pos": (int(matches.group("x")), int(matches.group("y"))),
        "cost": int(matches.group("price")),
    }


def handle_round(matches):
    try:
        round_num = int(matches.group("name"))
        if round_num < 1:
            print(f"Invalid round {round_num}, skipping!")
            return None
    except ValueError:
        print(f"NaN round {matches.group('name')}, skipping!")
        return None
    return {
        "action": "await_round",
        "round": round_num,
    }


def handle_speed(matches):
    return {
        "action": "speed",
        "speed": matches.group("name"),
        "cost": 0,
    }


def convertBTD6InstructionsFile(filename, targetResolution):
    fileConfig = parseBTD6InstructionFileName(filename)
    if not fileConfig:
        return False
    if not exists(filename):
        return False

    newFileName = getBTD6InstructionsFileNameByConfig(
        fileConfig, resolution=getResolutionString(targetResolution)
    )

    if exists(newFileName):
        return False

    fp = open(filename, "r")
    rawInputFile = fp.read()
    fp.close()

    output = convertPositionsInString(
        rawInputFile,
        [int(x) for x in fileConfig["resolution"].split("x")],
        targetResolution,
    )

    fp = open(newFileName, "w")
    fp.write(output)
    fp.close()

    return True


def getMonkeyUpgradeRequirements(monkeys):
    monkeyUpgradeRequirements = {}
    for monkey in monkeys:
        if monkeys[monkey]["type"] == "hero":
            continue
        if not monkeys[monkey]["type"] in monkeyUpgradeRequirements:
            monkeyUpgradeRequirements[monkeys[monkey]["type"]] = np.array(
                monkeys[monkey]["upgrades"]
            )
        else:
            monkeyUpgradeRequirements[monkeys[monkey]["type"]] = np.maximum(
                monkeyUpgradeRequirements[monkeys[monkey]["type"]],
                np.array(monkeys[monkey]["upgrades"]),
            )
    for monkey in monkeyUpgradeRequirements:
        monkeyUpgradeRequirements[monkey] = monkeyUpgradeRequirements[monkey].tolist()
    return monkeyUpgradeRequirements


def monkeyUpgradesToString(upgrades):
    return str(upgrades[0]) + "-" + str(upgrades[1]) + "-" + str(upgrades[2])


def getHadDefeats(playthrough, playthroughLog):
    if (
        not playthrough["filename"] in playthroughLog
        or not playthrough["gamemode"] in playthroughLog[playthrough["filename"]]
    ):
        return False
    return (
        playthroughLog[playthrough["filename"]][playthrough["gamemode"]]["defeats"] > 0
    )


def getAveragePlaythroughTime(playthrough):
    if not playthrough["filename"] in playthroughStats:
        return -1

    times = []
    for resolution in playthroughStats[playthrough["filename"]]:
        if not re.search(r"\d+x\d+", resolution):
            continue
        if (
            playthrough["gamemode"]
            in playthroughStats[playthrough["filename"]][resolution]
        ):
            times = [
                *times,
                *playthroughStats[playthrough["filename"]][resolution][
                    playthrough["gamemode"]
                ]["win_times"],
            ]
    return np.average(times or [-1])


def getHighestValuePlaythrough(
    allAvailablePlaythroughs, mapname, playthroughLog, preferNoMK=True
):
    highestValuePlaythrough = None
    highestValuePlaythroughValue = 0
    highestValuePlaythroughTime = -1
    highestValueNoDefeatsPlaythrough = None
    highestValueNoDefeatsPlaythroughValue = 0
    highestValueNoDefeatsPlaythroughTime = 0

    if mapname not in allAvailablePlaythroughs:
        return None

    for gamemode in allAvailablePlaythroughs[mapname]:
        for playthrough in allAvailablePlaythroughs[mapname][gamemode]:
            averageTime = getAveragePlaythroughTime(playthrough)
            if not getHadDefeats(playthrough, playthroughLog):
                if gamemodes[gamemode]["value"] > highestValueNoDefeatsPlaythroughValue:
                    highestValueNoDefeatsPlaythroughValue = gamemodes[gamemode]["value"]
                    highestValueNoDefeatsPlaythrough = playthrough
                    highestValueNoDefeatsPlaythroughTime = averageTime
                elif (
                    preferNoMK
                    and not highestValueNoDefeatsPlaythrough["fileConfig"]["noMK"]
                    and playthrough["fileConfig"]["noMK"]
                ):
                    highestValueNoDefeatsPlaythroughValue = gamemodes[gamemode]["value"]
                    highestValueNoDefeatsPlaythrough = playthrough
                    highestValueNoDefeatsPlaythroughTime = averageTime
                elif (
                    (
                        not preferNoMK
                        or highestValueNoDefeatsPlaythrough["fileConfig"]["noMK"]
                        is playthrough["fileConfig"]["noMK"]
                        or playthrough["fileConfig"]["noMK"]
                    )
                    and gamemodes[gamemode]["value"]
                    == highestValueNoDefeatsPlaythroughValue
                    and averageTime != -1
                    and (
                        averageTime < highestValueNoDefeatsPlaythroughTime
                        or highestValueNoDefeatsPlaythroughTime == -1
                    )
                ):
                    highestValueNoDefeatsPlaythroughValue = gamemodes[gamemode]["value"]
                    highestValueNoDefeatsPlaythrough = playthrough
                    highestValueNoDefeatsPlaythroughTime = averageTime
            else:
                if gamemodes[gamemode]["value"] > highestValuePlaythroughValue:
                    highestValuePlaythroughValue = gamemodes[gamemode]["value"]
                    highestValuePlaythrough = playthrough
                    highestValuePlaythroughTime = averageTime
                elif (
                    preferNoMK
                    and not highestValuePlaythrough["fileConfig"]["noMK"]
                    and playthrough["fileConfig"]["noMK"]
                ):
                    highestValuePlaythroughValue = gamemodes[gamemode]["value"]
                    highestValuePlaythrough = playthrough
                    highestValuePlaythroughTime = averageTime
                elif (
                    (
                        not preferNoMK
                        or highestValuePlaythrough["fileConfig"]["noMK"]
                        is playthrough["fileConfig"]["noMK"]
                        or playthrough["fileConfig"]["noMK"]
                    )
                    and gamemodes[gamemode]["value"] == highestValuePlaythroughValue
                    and averageTime != -1
                    and (
                        averageTime < highestValuePlaythroughTime
                        or highestValuePlaythroughTime == -1
                    )
                ):
                    highestValuePlaythroughValue = gamemodes[gamemode]["value"]
                    highestValuePlaythrough = playthrough
                    highestValuePlaythroughTime = averageTime

    return highestValueNoDefeatsPlaythrough or highestValuePlaythrough


def updatePlaythroughValidationStatus(
    playthroughFile, validationStatus, resolution=getResolutionString()
):
    global playthroughStats
    if playthroughFile not in playthroughStats:
        playthroughStats[playthroughFile] = {}
    if resolution not in playthroughStats[playthroughFile]:
        playthroughStats[playthroughFile][resolution] = {"validation_result": False}
    playthroughStats[playthroughFile][resolution][
        "validation_result"
    ] = validationStatus
    fp = open("playthrough_stats.json", "w")
    fp.write(json.dumps(playthroughStats, indent=4))
    fp.close()


def updateStatsFile(
    playthroughFile, thisPlaythroughStats, resolution=getResolutionString()
):
    global playthroughStats
    if playthroughFile not in playthroughStats:
        playthroughStats[playthroughFile] = {}
    if resolution not in playthroughStats[playthroughFile]:
        playthroughStats[playthroughFile][resolution] = {"validation_result": False}
    if (
        thisPlaythroughStats["gamemode"]
        not in playthroughStats[playthroughFile][resolution]
    ):
        playthroughStats[playthroughFile][resolution][
            thisPlaythroughStats["gamemode"]
        ] = {"attempts": 0, "wins": 0, "win_times": []}
    if thisPlaythroughStats["result"] == PlaythroughResult.WIN:
        playthroughStats[playthroughFile][resolution][thisPlaythroughStats["gamemode"]][
            "attempts"
        ] += 1
        playthroughStats[playthroughFile][resolution][thisPlaythroughStats["gamemode"]][
            "wins"
        ] += 1
        totalTime = 0
        lastStart = -1
        for stateChange in thisPlaythroughStats["time"]:
            if stateChange[0] == "start" and lastStart == -1:
                lastStart = stateChange[1]
            elif stateChange[0] == "stop" and lastStart != -1:
                totalTime += stateChange[1] - lastStart
                lastStart = -1
        playthroughStats[playthroughFile][resolution][thisPlaythroughStats["gamemode"]][
            "win_times"
        ].append(totalTime)
    else:
        playthroughStats[playthroughFile][resolution][thisPlaythroughStats["gamemode"]][
            "attempts"
        ] += 1
    fp = open("playthrough_stats.json", "w")
    fp.write(json.dumps(playthroughStats, indent=4))
    fp.close()


def checkForSingleMonkeyGroup(monkeys):
    types = list(
        filter(
            lambda x: x != "-",
            list(
                map(
                    lambda monkey: (
                        towers["monkeys"][monkeys[monkey]["type"]]["type"]
                        if monkeys[monkey]["type"] != "hero"
                        else "-"
                    ),
                    monkeys,
                )
            ),
        )
    )

    if len(set(types)) == 1:
        return types[0]
    else:
        return None


def checkForSingleMonkeyType(monkeys):
    types = list(
        filter(
            lambda x: x != "hero",
            list(map(lambda monkey: monkeys[monkey]["type"], monkeys)),
        )
    )

    if len(set(types)) == 1:
        return types[0]
    else:
        return None


def listBTD6InstructionsFileCompatability(filename):
    fileConfig = parseBTD6InstructionFileName(filename)
    mapConfig = parseBTD6InstructionsFile(filename)
    singleMonkeyGroup = checkForSingleMonkeyGroup(mapConfig["monkeys"])

    compatibleGamemodes = []
    if fileConfig["gamemode"] == "chimps":
        compatibleGamemodes = ["hard", "medium", "easy"]
    elif fileConfig["gamemode"] == "hard":
        compatibleGamemodes = ["medium", "easy"]
    elif fileConfig["gamemode"] == "medium":
        compatibleGamemodes = ["easy"]
    elif fileConfig["gamemode"] == "magic_monkeys_only":
        compatibleGamemodes = ["hard", "medium", "easy"]
    elif fileConfig["gamemode"] == "double_hp_moabs":
        compatibleGamemodes = ["hard", "medium", "easy"]
    elif fileConfig["gamemode"] == "half_cash":
        compatibleGamemodes = ["hard", "medium", "easy"]
    elif fileConfig["gamemode"] == "impoppable":
        compatibleGamemodes = ["hard", "medium", "easy"]
    elif fileConfig["gamemode"] == "military_only":
        compatibleGamemodes = ["medium", "easy"]
    elif fileConfig["gamemode"] == "primary_only":
        compatibleGamemodes = ["easy"]

    if (
        fileConfig["gamemode"]
        in ["hard", "double_hp_moabs", "half_cash", "impoppable", "chimps"]
        and singleMonkeyGroup
        and singleMonkeyGroup == "magic"
    ):
        compatibleGamemodes.append("magic_monkeys_only")
    elif (
        fileConfig["gamemode"]
        in ["medium", "hard", "double_hp_moabs", "half_cash", "impoppable", "chimps"]
        and singleMonkeyGroup
        and singleMonkeyGroup == "military"
    ):
        compatibleGamemodes.append("military_only")
    elif (
        fileConfig["gamemode"]
        in [
            "easy",
            "medium",
            "hard",
            "double_hp_moabs",
            "half_cash",
            "impoppable",
            "chimps",
        ]
        and singleMonkeyGroup
        and singleMonkeyGroup == "primary"
    ):
        compatibleGamemodes.append("primary_only")

    compatibleGamemodes.append(fileConfig["gamemode"])

    return compatibleGamemodes


def checkBTD6InstructionsFileCompatability(filename, gamemode):
    return gamemode in listBTD6InstructionsFileCompatability(filename)


def canUserUsePlaythrough(playthrough):
    if (
        not playthrough["fileConfig"]["map"] in userConfig["unlocked_maps"]
        or not userConfig["unlocked_maps"][playthrough["fileConfig"]["map"]]
    ):
        return False
    mapConfig = parseBTD6InstructionsFile(playthrough["filename"])
    if "hero" in mapConfig and (
        not mapConfig["hero"] in userConfig["heros"]
        or not userConfig["heros"][mapConfig["hero"]]
    ):
        return False
    return True


def getMedalStatus(mapname, gamemode):
    return (
        mapname in userConfig["medals"]
        and gamemode in userConfig["medals"][mapname]
        and userConfig["medals"][mapname][gamemode]
    )


def updateMedalStatus(mapname, gamemode, status=True):
    if getMedalStatus(mapname, gamemode) == status:
        return
    if mapname not in userConfig["medals"]:
        userConfig["medals"][mapname] = {}
    if gamemode not in userConfig["medals"][mapname]:
        userConfig["medals"][mapname][gamemode] = False
    userConfig["medals"][mapname][gamemode] = status
    fp = open("userconfig.json", "w")
    fp.write(json.dumps(userConfig, indent=4))
    fp.close()


def canUserAccessGamemode(mapname, gamemode):
    if mapname not in userConfig["medals"]:
        return False
    if gamemode in ["easy", "medium", "hard"]:
        return True
    if getMedalStatus(mapname, gamemode):
        return True
    if (
        (gamemode == "primary_only" and getMedalStatus(mapname, "easy"))
        or (gamemode == "deflation" and getMedalStatus(mapname, "primary_only"))
        or (gamemode == "easy_sandbox" and getMedalStatus(mapname, "easy"))
        or (gamemode == "military_only" and getMedalStatus(mapname, "medium"))
        or (gamemode == "apopalypse" and getMedalStatus(mapname, "military_only"))
        or (gamemode == "reverse" and getMedalStatus(mapname, "medium"))
        or (gamemode == "medium_sandbox" and getMedalStatus(mapname, "reverse"))
        or (gamemode == "hard_sandbox" and getMedalStatus(mapname, "hard"))
        or (gamemode == "magic_monkeys_only" and getMedalStatus(mapname, "hard"))
        or (
            gamemode == "double_hp_moabs"
            and getMedalStatus(mapname, "magic_monkeys_only")
        )
        or (gamemode == "half_cash" and getMedalStatus(mapname, "double_hp_moabs"))
        or (gamemode == "alternate_bloons_rounds" and getMedalStatus(mapname, "hard"))
        or (
            gamemode == "impoppable"
            and getMedalStatus(mapname, "alternate_bloons_rounds")
        )
        or (gamemode == "chimps" and getMedalStatus(mapname, "impoppable"))
    ):
        return True
    return False


def getAvailableSandbox(mapname, restricted_to=None):
    for gamemode in restricted_to if restricted_to is not None else sandboxGamemodes:
        if canUserAccessGamemode(mapname, gamemode):
            return gamemode
    return None


def getAllAvailablePlaythroughs(additionalDirs=[], considerUserConfig=False):
    playthroughs = {}
    files = []
    for dir in ["monkeymanager/maps", *additionalDirs]:
        if exists(dir):
            files = [*files, *[dir + "/" + x for x in os.listdir(dir)]]

    for filename in files:
        fileConfig = parseBTD6InstructionFileName(filename)
        if considerUserConfig and not canUserUsePlaythrough(
            {"filename": filename, "fileConfig": fileConfig}
        ):
            continue
        if fileConfig["map"] not in playthroughs:
            playthroughs[fileConfig["map"]] = {}
        compatibleGamemodes = listBTD6InstructionsFileCompatability(filename)
        for gamemode in compatibleGamemodes:
            if considerUserConfig and not canUserAccessGamemode(
                fileConfig["map"], gamemode
            ):
                continue
            if gamemode not in playthroughs[fileConfig["map"]]:
                playthroughs[fileConfig["map"]][gamemode] = []
            playthroughs[fileConfig["map"]][gamemode].append(
                {
                    "filename": filename,
                    "fileConfig": fileConfig,
                    "gamemode": gamemode,
                    "isOriginalGamemode": gamemode == fileConfig["gamemode"],
                }
            )

    return playthroughs


def filterAllAvailablePlaythroughs(
    playthroughs,
    monkeyKnowledgeEnabled,
    handlePlaythroughValidation,
    categoryRestriction,
    gamemodeRestriction,
    heroWhitelist=None,
    requiredFlags=None,
    onlyOriginalGamemodes=False,
    resolution=getResolutionString(),
):
    filteredPlaythroughs = {}

    for mapname in playthroughs:
        if categoryRestriction and maps[mapname]["category"] != categoryRestriction:
            continue
        for gamemode in playthroughs[mapname]:
            if gamemodeRestriction and gamemode != gamemodeRestriction:
                continue
            for playthrough in playthroughs[mapname][gamemode]:
                if not playthrough["fileConfig"]["noMK"] and not monkeyKnowledgeEnabled:
                    continue
                if heroWhitelist:
                    mapConfig = parseBTD6InstructionsFile(playthrough["filename"])
                    if "hero" in mapConfig and mapConfig["hero"] not in heroWhitelist:
                        continue
                if requiredFlags and not all(
                    [x in playthrough["fileConfig"] for x in requiredFlags]
                ):
                    continue
                if onlyOriginalGamemodes and not playthrough["isOriginalGamemode"]:
                    continue
                if (
                    handlePlaythroughValidation != ValidatedPlaythroughs.INCLUDE_ALL
                    and (
                        (
                            handlePlaythroughValidation
                            == ValidatedPlaythroughs.EXCLUDE_NON_VALIDATED
                            and (
                                playthrough["filename"] not in playthroughStats
                                or resolution
                                not in playthroughStats[playthrough["filename"]]
                                or "validation_result"
                                not in playthroughStats[playthrough["filename"]][
                                    resolution
                                ]
                                or not playthroughStats[playthrough["filename"]][
                                    resolution
                                ]["validation_result"]
                            )
                        )
                        or (
                            handlePlaythroughValidation
                            == ValidatedPlaythroughs.EXCLUDE_VALIDATED
                            and (
                                playthrough["filename"] in playthroughStats
                                and resolution
                                in playthroughStats[playthrough["filename"]]
                                and "validation_result"
                                in playthroughStats[playthrough["filename"]][resolution]
                                and playthroughStats[playthrough["filename"]][
                                    resolution
                                ]["validation_result"]
                            )
                        )
                    )
                ):
                    continue
                if mapname not in filteredPlaythroughs:
                    filteredPlaythroughs[mapname] = {}
                if gamemode not in filteredPlaythroughs[mapname]:
                    filteredPlaythroughs[mapname][gamemode] = []
                filteredPlaythroughs[mapname][gamemode].append(playthrough)

    return filteredPlaythroughs


def allPlaythroughsToList(playthroughs):
    playthroughList = []
    for mapname in playthroughs:
        for gamemode in playthroughs[mapname]:
            for playthrough in playthroughs[mapname][gamemode]:
                playthroughList.append(playthrough)

    return playthroughList


def getRoundTotalBaseXP(round):
    if round < 0:
        return 0
    xp = (min(round + 1, 21) ** 2 + min(round + 1, 21)) / 2 * 20
    if round > 20:
        xp += (min(round - 20, 30)) * 21 * 20 + (
            min(round - 20, 30) ** 2 + min(round - 20, 30)
        ) / 2 * 40
        if round > 50:
            xp += (min(round - 50, 50)) * (21 * 20 + 30 * 40) + (
                min(round - 50, 50) ** 2 + min(round - 50, 50)
            ) / 2 * 90
    return xp


def getPlaythroughXP(gamemode, mapcategory):
    xp = 0

    if gamemode in ["easy", "primary_only"]:
        xp = getRoundTotalBaseXP(40) - getRoundTotalBaseXP(0)
    elif gamemode in ["deflation"]:
        xp = getRoundTotalBaseXP(60) - getRoundTotalBaseXP(30)
    elif gamemode in ["medium", "military_only", "reverse", "apopalypse"]:
        xp = getRoundTotalBaseXP(60) - getRoundTotalBaseXP(0)
    elif gamemode in [
        "hard",
        "magic_monkeys_only",
        "double_hp_moabs",
        "half_cash",
        "alternate_bloons_rounds",
    ]:
        xp = getRoundTotalBaseXP(80) - getRoundTotalBaseXP(2)
    elif gamemode in ["impoppable", "chimps"]:
        xp = getRoundTotalBaseXP(100) - getRoundTotalBaseXP(5)

    if mapcategory == "intermediate":
        xp = xp * 1.1
    elif mapcategory == "advanced":
        xp = xp * 1.2
    elif mapcategory == "expert":
        xp = xp * 1.3
    return xp


def getPlaythroughMonkeyMoney(gamemode, mapcategory):
    if gamemode not in gamemodes:
        return 0

    replayMonkeyMoney = {
        "easy": {"beginner": 15, "intermediate": 30, "advanced": 45, "expert": 60},
        "medium": {"beginner": 25, "intermediate": 50, "advanced": 75, "expert": 100},
        "hard": {"beginner": 40, "intermediate": 80, "advanced": 120, "expert": 160},
        "impoppable": {
            "beginner": 60,
            "intermediate": 120,
            "advanced": 180,
            "expert": 240,
        },
    }

    if mapcategory not in replayMonkeyMoney["easy"]:
        return 0

    return replayMonkeyMoney[gamemodes[gamemode]["cash_group"]][mapcategory]


def getPlaythroughXPPerHour(playthrough):
    averageTime = getAveragePlaythroughTime(playthrough)
    if averageTime == -1:
        return 0
    return (
        3600
        / averageTime
        * getPlaythroughXP(
            playthrough["gamemode"], maps[playthrough["fileConfig"]["map"]]["category"]
        )
    )


def getPlaythroughMonkeyMoneyPerHour(playthrough):
    averageTime = getAveragePlaythroughTime(playthrough)
    if averageTime == -1:
        return 0
    return (
        3600
        / averageTime
        * getPlaythroughMonkeyMoney(
            playthrough["gamemode"], maps[playthrough["fileConfig"]["map"]]["category"]
        )
    )


def sortPlaythroughsByGain(playthroughs, gainFunc):
    return sorted(
        map(lambda x: {**x, "value": gainFunc(x)}, playthroughs),
        key=lambda x: x["value"],
        reverse=True,
    )


def sortPlaythroughsByMonkeyMoneyGain(playthroughs):
    return sortPlaythroughsByGain(playthroughs, getPlaythroughMonkeyMoneyPerHour)


def sortPlaythroughsByXPGain(playthroughs):
    return sortPlaythroughsByGain(playthroughs, getPlaythroughXPPerHour)


def findImageInImage(img, subImg):
    result = cv2.matchTemplate(img, subImg, cv2.TM_SQDIFF_NORMED)
    return [cv2.minMaxLoc(result)[i] for i in [0, 2]]


def findMapForPxPos(category, page, pxpos):
    if category not in mapsByPos or page not in mapsByPos[category]:
        return None
    bestFind = None
    bestFindDist = 100000
    for iTmp in mapsByPos[category][page]:
        mapname = mapsByPos[category][page][iTmp]
        pos = imageAreas["click"]["map_positions"][maps[mapname]["pos"]]
        if pos[0] < pxpos[0] and pos[1] < pxpos[1]:
            dist = math.dist(pos, pxpos)
            if dist < bestFindDist:
                bestFind = mapname
                bestFindDist = dist
    return bestFind


lastLineRewrite = False


def customPrint(text, end="\n", rewriteLine=False):
    global lastLineRewrite
    if lastLineRewrite and not rewriteLine:
        print()
    print(
        ("\r" if rewriteLine else "")
        + time.strftime("[%Y-%m-%d %H:%M:%S] ")
        + str(text),
        end=end,
    )
    lastLineRewrite = rewriteLine


def convertPositionsInString(rawStr, nativeResolution, resolution):
    return re.sub(
        r"(?P<x>\d+), (?P<y>\d+)",
        lambda match: str(
            round(int(match.group("x")) * resolution[0] / nativeResolution[0])
        )
        + ", "
        + str(round(int(match.group("y")) * resolution[1] / nativeResolution[1])),
        rawStr,
    )


def setMonkeyKnowledgeStatus(status):
    global monkeyKnowledgeEnabled
    monkeyKnowledgeEnabled = status


def getMonkeyKnowledgeStatus():
    return monkeyKnowledgeEnabled


def keyToAHK(x):
    return "{sc" + hex(x).replace("0x", "") + "}" if isinstance(x, int) else x


def sendKey(key):
    ahk.send(keyToAHK(key), key_delay=15, key_press_duration=30, send_mode="Event")


def mapnameToKeyname(mapname):
    return (
        "".join([(x if x not in ["'", "#"] else "") for x in mapname])
        .replace(" ", "_")
        .lower()
    )


def mapsByCategoryToMaplist(mapsByCategory, maps):
    newMaps = {}
    for category in mapsByCategory:
        currentPos = 0
        currentPage = 0
        for mapname in mapsByCategory[category]:
            newMaps[mapname] = {
                "category": category,
                "name": maps[mapname]["name"],
                "page": currentPage,
                "pos": currentPos,
            }
            currentPos += 1
            if currentPos >= 6:
                currentPos = 0
                currentPage += 1
    return newMaps


def upgradeRequiresConfirmation(monkey, path):
    if "upgrade_confirmation" not in towers["monkeys"][monkey["type"]]:
        return False
    if monkey["upgrades"][path] - 1 == -1:
        return False
    if monkey["upgrades"][path] - 1 >= 5:
        return True
    return towers["monkeys"][monkey["type"]]["upgrade_confirmation"][path][
        monkey["upgrades"][path] - 1
    ]


def isBTD6Window(name):
    return name in ["BloonsTD6", "BloonsTD6-Epic"]


monkeyKnowledgeEnabled = False

sandboxGamemodes = {
    "easy_sandbox": {"group": "easy"},
    "medium_sandbox": {"group": "medium"},
    "hard_sandbox": {"group": "hard"},
}

maps = json.load(open("monkeymanager/namespaces/maps.json"))
gamemodes = json.load(open("monkeymanager/namespaces/gamemodes.json"))
keybinds = json.load(open("monkeymanager/namespaces/keybinds.json"))
towers = json.load(open("monkeymanager/namespaces/towers.json"))
allImageAreas = json.load(open("monkeymanager/namespaces/image_areas.json"))
if getResolutionString() in allImageAreas:
    imageAreas = allImageAreas[getResolutionString()]
else:
    imageAreas = json.loads(
        convertPositionsInString(
            json.dumps(allImageAreas["2560x1440"]), (2560, 1440), pyautogui.size()
        )
    )

playthroughStats = {}
if exists("playthrough_stats.json"):
    playthroughStats = json.load(open("playthrough_stats.json"))

userConfig = {
    "monkey_knowledge": {},
    "heros": {},
    "unlocked_maps": {},
    "unlocked_monkey_upgrades": {},
}
if exists("userconfig.json"):
    userConfig = json.load(open("userconfig.json"))

mapsByCategory = {}
for mapname in maps:
    if not maps[mapname]["category"] in mapsByCategory:
        mapsByCategory[maps[mapname]["category"]] = []
    mapsByCategory[maps[mapname]["category"]].append(mapname)

mapsByPos = {}
for mapname in maps:
    if not maps[mapname]["category"] in mapsByPos:
        mapsByPos[maps[mapname]["category"]] = {}
    if not maps[mapname]["page"] in mapsByPos[maps[mapname]["category"]]:
        mapsByPos[maps[mapname]["category"]][maps[mapname]["page"]] = {}
    mapsByPos[maps[mapname]["category"]][maps[mapname]["page"]][
        maps[mapname]["pos"]
    ] = mapname


categoryPages = {}
for category in mapsByCategory:
    categoryPages[category] = (
        max(map(lambda x: maps[x]["page"], mapsByCategory[category])) + 1
    )
