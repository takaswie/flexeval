from __future__ import annotations

import re
from collections import defaultdict
from math import exp

import tqdm
from loguru import logger
from numpy import average

from flexeval.core.language_model import LanguageModel
from flexeval.core.prompt_template import PromptTemplate

from .base import Metric, MetricResult
from .llm_score import prepare_chat_input_for_evaluator, prepare_text_input_for_evaluator


def calculate_weighted_average(
    evaluator_logprobs: dict[str, float | None], valid_score_range: tuple[int, int] | None
) -> tuple[float | None, dict[int, float]]:
    """For each token and its logprob, check whether the token in valid_score_range
    and calculate weighted score among valid scores and their logprobs.

    Args:
        evaluator_logprobs: Keys are valid tokens, and values are their logprobs.
        valid_score_range: The scope of scores. If None, any of the score is accepted.

    Return None if all of the tokens are not valid as score.
    """
    score_prob_dict: dict[int, float] = {}
    for token, logprob in evaluator_logprobs.items():
        if logprob is None:
            continue

        matched = re.match(r"(\d)+", token)
        if not matched:
            continue

        parsed_score = int(token)
        if valid_score_range and not valid_score_range[0] <= parsed_score <= valid_score_range[1]:
            continue

        score_prob_dict[parsed_score] = exp(logprob)

    if len(score_prob_dict) == 0:
        return None, score_prob_dict

    return average(list(score_prob_dict.keys()), weights=list(score_prob_dict.values())), score_prob_dict


def summarize_evaluator_geval_scores(
    evaluator_score_list: list[float | None],
    task_inputs_list: list[dict[str, str]],
    category_key: str | None = None,
) -> dict[str, float]:
    """Summarize evaluator_score_list. If category_key is given, return
    category-wise mean score as well as overall mean score.
    """

    # compute overall mean score
    all_valid_scores: list[int] = [s for s in evaluator_score_list if s is not None]
    llm_score = sum(all_valid_scores) / len(all_valid_scores)
    num_failed_score_parses = len(evaluator_score_list) - len(all_valid_scores)
    summary = {"llm_geval_score": llm_score, "num_failed_score_parses": num_failed_score_parses}

    # compute category-wise mean score if category_key is given
    category2valid_scores: dict[str, list[int]] = defaultdict(list)
    for score, task_inputs in zip(evaluator_score_list, task_inputs_list):
        if score is None or category_key is None:
            continue
        if category_key in task_inputs:
            category2valid_scores[task_inputs[category_key]].append(score)

    category2mean_score: dict[str, float] = {}
    for category, valid_scores in category2valid_scores.items():
        category2mean_score[category] = sum(valid_scores) / len(valid_scores)

    for category, mean_score in category2mean_score.items():
        summary[f"llm_geval_score/{category}"] = mean_score
    return summary


def generate_evaluation_logprobs(
    evaluator_input_list: list[str] | list[list[dict[str, str]]],
    language_model: LanguageModel,
    valid_labels: list[str],
    disable_tqdm: bool = False,
    desc_for_tqdm: str | None = None,
) -> list[dict[str, float]]:
    """Generate evaluation logprobs for each input in evaluator_input_list.
    Restrict to valid labels for computation of logprobs

    - If evaluator_input_list contains a list of plain texts, use
      language_model.batch_compute_log_probs() to generate evaluation logprobs.
    - If evaluator_input_list contains a list of chat message dictionaries,
      use language_model.batch_compute_chat_log_probs().
    """

    with tqdm.tqdm(
        total=len(evaluator_input_list),
        disable=disable_tqdm,
        desc=desc_for_tqdm,
    ) as pbar:
        evaluator_logprobs_list: list[dict[str, float]] = []
        for evaluator_input in evaluator_input_list:
            if isinstance(evaluator_input, str):
                evaluator_logprobs = language_model.batch_compute_log_probs(
                    valid_labels,  # for openai models, len(valid_labels) <= 20 due to constraint
                    [evaluator_input]
                    * len(valid_labels),  # we have to provide len(valid_labels) same inputs for generate logprob
                )
            else:
                evaluator_logprobs = language_model.batch_compute_chat_log_probs(
                    [evaluator_input for _ in valid_labels],
                    [{"role": "assistant", "content": label} for label in valid_labels],
                )
            evaluator_logprobs_list += [dict(zip(valid_labels, evaluator_logprobs))]
            pbar.update(1)
    return evaluator_logprobs_list


class LLMGEvalScore(Metric):
    """Let LanguageModel evaluate the output of another LanguageModel.
    Unlike LLMScore, this metric let the model output logprobs for all valid scores and
    calculate weighted score among them.
    Note that due to constraint for OpenAI models, the number of valid scores must not exceed 20.
    For detail, see https://aclanthology.org/2023.emnlp-main.153/

    You can specify the evaluation criteria in `PromptTemplate`.

    Args:
        language_model (required): An instance of `LanguageModel` to evaluate the output of the model.
        prompt_template (required): An instance of `PromptTemplate` to embed the input for the evaluator.
        valid_score_range (required): A tuple of two integers representing the valid score range.
            If the parsed score is out of the range, it will be ignored.
        disable_tqdm: Whether to disable the progress bar.
        category_key: A key to create category-wise mean score.
            The category key is expected to be in task inputs.

    Examples:
        >>> from flexeval import LLMGEvalScore, HuggingFaceLM, Jinja2PromptTemplate
        >>> language_model = HuggingFaceLM("Qwen/Qwen2.5-0.5B-Instruct")
        >>> template = "Evaluate the quality of this text.\\n`{{ lm_output }}`\\nOutput only a number from 1 to 5."
        >>> prompt_template = Jinja2PromptTemplate(template)
        >>> llm_score = LLMGEvalScore(language_model, prompt_template, [1,5])
        >>> lm_outputs = ["Hello, world!", "Good morning!"]
        >>> llm_score.evaluate(lm_outputs)
        MetricResult(
            summary={'llm_geval_score': 3.9109915840526117, 'num_failed_score_parses': 0},
            instance_details=[
                {
                    'llm_geval_score': 3.580269080315562,
                    'llm_geval_score_input': 'Evaluate the quality of this text...',
                    'llm_geval_score_logprobs': {
                        '1': -95.71658325195312,
                        '2': -94.330322265625,
                        '3': -94.506103515625,
                        '4': -94.219970703125,
                        '5': -93.872802734375
                    }
                },
                {
                    'llm_geval_score': 4.241714087789661,
                    'llm_geval_score_input': 'Evaluate the quality of this text...',
                    'llm_geval_score_logprobs': {
                        '1': -99.65960693359375,
                        '2': -97.5340576171875,
                        '3': -97.42755126953125,
                        '4': -97.17465209960938,
                        '5': -95.7857666015625
                    }
                }
            ]
        )
    """

    def __init__(
        self,
        language_model: LanguageModel,
        prompt_template: PromptTemplate,
        valid_score_range: tuple[int, int],
        disable_tqdm: bool = False,
        category_key: str | None = None,
    ) -> None:
        self.language_model = language_model
        self.prompt_template = prompt_template
        self.disable_tqdm = disable_tqdm
        self.valid_score_range = valid_score_range
        self.category_key = category_key

        self.valid_labels = [str(score) for score in range(valid_score_range[0], valid_score_range[1] + 2)]

    def evaluate(
        self,
        lm_outputs: list[str],
        references_list: list[list[str]] | None = None,
        task_inputs_list: list[dict[str, str]] | None = None,
    ) -> MetricResult:
        if task_inputs_list is None:
            task_inputs_list = [{} for _ in lm_outputs]
        if references_list is None:
            references_list = [[] for _ in lm_outputs]

        evaluator_input_list: list[str] = prepare_text_input_for_evaluator(
            lm_outputs, references_list, task_inputs_list, self.prompt_template
        )
        evaluator_logprobs_list: list[dict[str, float]] = generate_evaluation_logprobs(
            evaluator_input_list,
            self.language_model,
            self.valid_labels,
            self.disable_tqdm,
            "Calculating logprobs",
        )

        evaluator_score_list: list[int | None] = []
        evaluator_probs_list: list[dict[int, float]] = []
        for evaluator_logprobs in evaluator_logprobs_list:
            evaluator_score, evaluator_probs = calculate_weighted_average(
                evaluator_logprobs,
                self.valid_score_range,
            )
            if evaluator_score is None:
                logger.warning(f"Failed to parse score from evaluator logprobs: {evaluator_logprobs}")
            evaluator_score_list.append(evaluator_score)
            evaluator_probs_list.append(evaluator_probs)

        summary = summarize_evaluator_geval_scores(
            evaluator_score_list,
            task_inputs_list,
            self.category_key,
        )

        return MetricResult(
            summary,
            instance_details=[
                {
                    "llm_geval_score": eval_score,
                    "llm_geval_score_input": eval_in,
                    "llm_geval_score_logprobs": eval_logprobs,
                    "llm_geval_score_probs": eval_probs,

                }
                for eval_score, eval_in, eval_logprobs, eval_probs in zip(
                    evaluator_score_list,
                    evaluator_input_list,
                    evaluator_logprobs_list,
                    evaluator_probs_list,
                )
            ],
        )

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(language_model={self.language_model}, prompt_template={self.prompt_template})"
        )


class ChatLLMGEvalScore(Metric):
    """A metric that evaluates the output of `LanguageModel.batch_generate_chat_response`.
    Unlike ChatLLMScore, this metric let the model output logprobs for all valid scores and
    calculate weighted score among them.
    Note that due to constraint for OpenAI models, the number of valid scores must not exceed 20.

    Args:
        language_model (required): An instance of `LanguageModel` to evaluate the output of the model.
        prompt_template (required): An instance of `PromptTemplate` to embed the input for the evaluator.
        valid_score_range (required): A tuple of two integers representing the valid score range.
            If the parsed score is out of the range, it will be ignored.
        system_message: A system message to be prepended to the input for the evaluator.
        disable_tqdm: Whether to disable the progress bar.
        category_key: A key to create category-wise mean score.
            The category key is expected to be in task inputs.


    Examples:
        >>> from flexeval import ChatLLMGEvalScore, HuggingFaceLM, Jinja2PromptTemplate
        >>> language_model = HuggingFaceLM("Qwen/Qwen2.5-0.5B-Instruct")
        >>> template = "Evaluate the quality of this text.\\n`{{ lm_output }}`\\nOutput only a number from 1 to 5."
        >>> prompt_template = Jinja2PromptTemplate(template)
        >>> system_message = "This is the system message."
        >>> llm_score = ChatLLMGEvalScore(language_model, prompt_template, [1,5], system_message)
        >>> lm_outputs = ["Hello, world!", "Good morning!"]
        >>> llm_score.evaluate(lm_outputs)
        MetricResult(
            summary={'llm_geval_score': 4.252030918369018, 'num_failed_score_parses': 0},
            instance_details=[
                {
                    'llm_geval_score': 4.220479925250702,
                    'llm_geval_score_input': [
                        {'role': 'system', 'content': 'This is the system message.'},
                        {'role': 'user', 'content': 'Evaluate the quality of this text...'}
                    ],
                    'llm_geval_score_logprobs': {
                        '1': -165.50238037109375,
                        '2': -165.77108764648438,
                        '3': -163.51712036132812,
                        '4': -164.01840209960938,
                        '5': -162.56027221679688
                    }
                },
                {
                    'llm_geval_score': 4.283581911487334,
                    'llm_geval_score_input': [
                        {'role': 'system', 'content': 'This is the system message.'},
                        {'role': 'user', 'content': 'Evaluate the quality of this text...'}
                    ],
                    'llm_geval_score_logprobs': {
                        '1': -173.6418914794922,
                        '2': -173.9242706298828,
                        '3': -172.4905242919922,
                        '4': -172.95521545410156,
                        '5': -171.043701171875
                    }
                }
            ])
    """

    def __init__(
        self,
        language_model: LanguageModel,
        prompt_template: PromptTemplate,
        valid_score_range: tuple[int, int],
        system_message: str | PromptTemplate | None = None,
        disable_tqdm: bool = False,
        category_key: str | None = None,
    ) -> None:
        self.language_model = language_model
        self.prompt_template = prompt_template
        self.system_message = system_message
        self.disable_tqdm = disable_tqdm
        self.valid_score_range = valid_score_range
        self.category_key = category_key
        self.prob_threshold = prob_threshold

        self.valid_labels = [str(score) for score in range(valid_score_range[0], valid_score_range[1] + 1)]

    def evaluate(
        self,
        lm_outputs: list[str],
        references_list: list[list[str]] | None = None,
        task_inputs_list: list[dict[str, str]] | None = None,
    ) -> MetricResult:
        if task_inputs_list is None:
            task_inputs_list = [{} for _ in lm_outputs]
        if references_list is None:
            references_list = [[] for _ in lm_outputs]

        evaluator_input_list = prepare_chat_input_for_evaluator(
            lm_outputs, references_list, task_inputs_list, self.prompt_template, self.system_message
        )
        evaluator_logprobs_list: list[dict[str, float]] = generate_evaluation_logprobs(
            evaluator_input_list,
            self.language_model,
            self.valid_labels,
            self.disable_tqdm,
            "Calculating logprobs",
        )

        evaluator_score_list: list[int | None] = []
        evaluator_probs_list: list[dict[int, float]] = []
        for evaluator_logprobs in evaluator_logprobs_list:
            evaluator_score, evaluator_probs = calculate_weighted_average(
                evaluator_logprobs,
                self.valid_score_range,
            )
            if evaluator_score is None:
                logger.warning(f"Failed to parse score from evaluator logprobs: {evaluator_logprobs}")
            evaluator_score_list.append(evaluator_score)
            evaluator_probs_list.append(evaluator_probs)

        summary = summarize_evaluator_geval_scores(
            evaluator_score_list,
            task_inputs_list,
            self.category_key,
        )

        return MetricResult(
            summary,
            instance_details=[
                {
                    "llm_geval_score": eval_score,
                    "llm_geval_score_input": eval_in,
                    "llm_geval_score_logprobs": eval_logprobs,
                    "llm_geval_score_probs": eval_probs,

                }
                for eval_score, eval_in, eval_logprobs, eval_probs in zip(
                    evaluator_score_list,
                    evaluator_input_list,
                    evaluator_logprobs_list,
                    evaluator_probs_list,
                )
            ],
        )

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(language_model={self.language_model}, prompt_template={self.prompt_template})"
        )
