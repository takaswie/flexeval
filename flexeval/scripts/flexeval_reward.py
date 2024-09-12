from __future__ import annotations

import os
import sys
from importlib.metadata import version
from pathlib import Path
from typing import Any, Dict

from jsonargparse import ActionConfigFile, ArgumentParser
from loguru import logger

from flexeval import LanguageModel, RewardEvalSetup
from flexeval.core.result_recorder.base import ResultRecorder
from flexeval.core.result_recorder.local_recorder import LocalRecorder
from flexeval.scripts.common import (
    Timer,
    get_env_metadata,
)


def calc_accuracy(predictions: list[str], answers: list[str]) -> float:
    return sum(prediction == answer for prediction, answer in zip(predictions, answers)) / len(answers)


def main() -> None:
    parser = ArgumentParser(parser_mode="jsonnet")
    parser.add_subclass_arguments(
        LanguageModel,
        nested_key="language_model",
        required=True,
        help="Language model",
    )
    parser.add_argument(
        "--eval_setup",
        type=RewardEvalSetup,
        help="A reward evaluation setup. ",
        enable_path=True,
    )
    parser.add_argument("--batch_size", type=int, default=4)
    # Saving arguments
    parser.add_argument(
        "--save_dir",
        type=str,
        default=None,
        help="Directory to save the outputs",
    )
    parser.add_argument(
        "--force",
        type=bool,
        default=False,
        help="Overwrite the save_dir if it exists",
    )
    parser.add_argument(
        "--result_recorder",
        type=ResultRecorder,
        default=None,
        help="Result recorder to save the evaluation results",
    )
    # Argument parsing arguments
    parser.add_argument(
        "--config",
        action=ActionConfigFile,
        help="Path to the config file",
    )
    # Metadata
    parser.add_argument(
        "--metadata",
        type=Dict[str, Any],
        default={},
        help="Metadata to save in config.json",
    )

    # Add the current directory to sys.path
    # to enable importing modules from the directory where this script is executed.
    sys.path.append(os.environ.get("ADDITIONAL_MODULES_PATH", str(Path.cwd())))

    args = parser.parse_args()
    logger.info(args)

    args_as_dict = args.as_dict()
    args_as_dict["metadata"].update(get_env_metadata())
    logger.info(f"flexeval version: {version('flexeval')}")

    # We instantiate the result_recorder first
    # to allow it to initialize global logging modules (e.g., wandb) that other classes might use.
    result_recorder = parser.instantiate_classes({"result_recorder": args.pop("result_recorder")}).result_recorder
    args = parser.instantiate_classes(args)
    args.result_recorder = result_recorder

    result_recorders: list[ResultRecorder] = []
    if args.save_dir is not None:
        result_recorders.append(LocalRecorder(args.save_dir, force=args.force))
    if args.result_recorder:
        result_recorders.append(args.result_recorder)

    for result_recorder in result_recorders:
        result_recorder.record_config(args_as_dict)

    with Timer() as timer:
        metrics, outputs = args.eval_setup.evaluate_lm(
            language_model=args.language_model,
        )
    metrics["elapsed_time"] = timer.time
    logger.info(f"Elapsed time: {timer.time:.2f} sec")

    for result_recorder in result_recorders:
        result_recorder.record_metrics(metrics)
        result_recorder.record_model_outputs(outputs)


if __name__ == "__main__":
    main()
