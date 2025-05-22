import dataclasses
import functools
import inspect
import os
import shutil
import tempfile

from argparse import Namespace
from unittest.mock import patch

import pytest
import typer

import gpt_engineer.applications.cli.main as main

from gpt_engineer.applications.cli.main import load_prompt
from gpt_engineer.core.default.disk_memory import DiskMemory
from gpt_engineer.core.prompt import Prompt


@functools.wraps(dataclasses.make_dataclass)
def dcommand(typer_f, **kwargs):
    required = True

    def field_desc(name, param):
        nonlocal required

        t = param.annotation or "typing.Any"
        if param.default.default is not ...:
            required = False
            return name, t, dataclasses.field(default=param.default.default)

        if not required:
            raise ValueError("Required value after optional")

        return name, t

    kwargs.setdefault("cls_name", typer_f.__name__)

    params = inspect.signature(typer_f).parameters
    kwargs["fields"] = [field_desc(k, v) for k, v in params.items()]

    @functools.wraps(typer_f)
    def dcommand_decorator(function_or_class):
        assert callable(function_or_class)

        ka = dict(kwargs)
        ns = Namespace(**(ka.pop("namespace", None) or {}))
        if isinstance(function_or_class, type):
            ka["bases"] = *ka.get("bases", ()), function_or_class
        else:
            ns.__call__ = function_or_class

        ka["namespace"] = vars(ns)
        return dataclasses.make_dataclass(**ka)

    return dcommand_decorator


@dcommand(main.main)
class DefaultArgumentsMain:
    def __call__(self):
        attribute_dict = vars(self)
        main.main(**attribute_dict)


def input_generator():
    yield "y"  # First response
    while True:
        yield "n"  # Subsequent responses


prompt_text = "Make a python program that writes 'hello' to a file called 'output.txt'"


class TestMain:
    #  Runs gpt-engineer cli interface for many parameter configurations, BUT DOES NOT CODEGEN! Only testing cli.
    def test_default_settings_generate_project(self, tmp_path, monkeypatch):
        p = tmp_path / "projects/example"
        p.mkdir(parents=True)
        (p / "prompt").write_text(prompt_text)
        args = DefaultArgumentsMain(str(p), llm_via_clipboard=True, no_execution=True)
        args()

    #  Runs gpt-engineer with improve mode and improves an existing project in the specified path.
    def test_improve_existing_project(self, tmp_path, monkeypatch):
        p = tmp_path / "projects/example"
        p.mkdir(parents=True)
        (p / "prompt").write_text(prompt_text)
        args = DefaultArgumentsMain(
            str(p), improve_mode=True, llm_via_clipboard=True, no_execution=True
        )
        args()

    #  Runs gpt-engineer with improve mode and improves an existing project in the specified path, with skip_file_selection
    def test_improve_existing_project_skip_file_selection(self, tmp_path, monkeypatch):
        p = tmp_path / "projects/example"
        p.mkdir(parents=True)
        (p / "prompt").write_text(prompt_text)
        args = DefaultArgumentsMain(
            str(p),
            improve_mode=True,
            llm_via_clipboard=True,
            no_execution=True,
            skip_file_selection=True,
        )
        args()
        assert args.skip_file_selection, "Skip_file_selection not set"

    #  Runs gpt-engineer with improve mode and improves an existing project in the specified path, with skip_file_selection
    def test_improve_existing_project_diff_timeout(self, tmp_path, monkeypatch):
        p = tmp_path / "projects/example"
        p.mkdir(parents=True)
        (p / "prompt").write_text(prompt_text)
        args = DefaultArgumentsMain(
            str(p),
            improve_mode=True,
            llm_via_clipboard=True,
            no_execution=True,
            diff_timeout=99,
        )
        args()
        assert args.diff_timeout == 99, "Diff timeout not set"

        # def improve_generator():
        #     yield "y"
        #     while True:
        #         yield "n"  # Subsequent responses
        #
        # gen = improve_generator()
        # monkeypatch.setattr("builtins.input", lambda _: next(gen))
        # p = tmp_path / "projects/example"
        # p.mkdir(parents=True)
        # (p / "prompt").write_text(prompt_text)
        # (p / "main.py").write_text("The program will be written in this file")
        # meta_p = p / META_DATA_REL_PATH
        # meta_p.mkdir(parents=True)
        # (meta_p / "file_selection.toml").write_text(
        #     """
        # [files]
        # "main.py" = "selected"
        #             """
        # )
        # os.environ["GPTE_TEST_MODE"] = "True"
        # simplified_main(str(p), "improve")
        # DiskExecutionEnv(path=p)
        # del os.environ["GPTE_TEST_MODE"]

    #  Runs gpt-engineer with lite mode and generates a project with only the main prompt.
    def test_lite_mode_generate_project(self, tmp_path, monkeypatch):
        p = tmp_path / "projects/example"
        p.mkdir(parents=True)
        (p / "prompt").write_text(prompt_text)
        args = DefaultArgumentsMain(
            str(p), lite_mode=True, llm_via_clipboard=True, no_execution=True
        )
        args()

    #  Runs gpt-engineer with clarify mode and generates a project after discussing the specification with the AI.
    def test_clarify_mode_generate_project(self, tmp_path, monkeypatch):
        p = tmp_path / "projects/example"
        p.mkdir(parents=True)
        (p / "prompt").write_text(prompt_text)
        args = DefaultArgumentsMain(
            str(p), clarify_mode=True, llm_via_clipboard=True, no_execution=True
        )
        args()

    #  Runs gpt-engineer with self-heal mode and generates a project after discussing the specification with the AI and self-healing the code.
    def test_self_heal_mode_generate_project(self, tmp_path, monkeypatch):
        p = tmp_path / "projects/example"
        p.mkdir(parents=True)
        (p / "prompt").write_text(prompt_text)
        args = DefaultArgumentsMain(
            str(p), self_heal_mode=True, llm_via_clipboard=True, no_execution=True
        )
        args()

    def test_clarify_lite_improve_mode_generate_project(self, tmp_path, monkeypatch):
        p = tmp_path / "projects/example"
        p.mkdir(parents=True)
        (p / "prompt").write_text(prompt_text)
        args = DefaultArgumentsMain(
            str(p),
            improve_mode=True,
            lite_mode=True,
            clarify_mode=True,
            llm_via_clipboard=True,
            no_execution=True,
        )
        pytest.raises(typer.Exit, args)

    #  Tests the creation of a log file in improve mode.

# Helper for default preprompt strings if not found by PrepromptsHolder mock
DEFAULT_CLARIFY_INITIAL_PROMPT = (
    "You are an AI assistant. The user has provided an initial prompt. "
    "Review it. If it's clear and actionable for code generation, respond with the exact phrase 'READY_TO_GENERATE'. "
    "Otherwise, ask a single, concise question to clarify the most critical ambiguity or missing piece of information. "
    "Do not offer to write code yet."
)
DEFAULT_CLARIFY_NEXT_STEP_PROMPT = (
    "You are an AI assistant. You have been in a dialogue to clarify software requirements. "
    "Review the entire conversation. If all critical ambiguities are resolved and you have "
    "enough information to generate the code, respond with the exact phrase 'READY_TO_GENERATE'. "
    "Otherwise, formulate the *next single most important* clarifying question to ask the user. "
    "Do not offer to write code yet."
)
DEFAULT_PHILOSOPHY_PROMPT = "This is the default philosophy prompt."


class TestClarifyMode:
    project_path_string = "projects/clarify_test"

    def _run_main_with_clarify_mocks(
        self,
        tmp_path,
        mock_ai_next,
        mock_input,
        mock_print,
        mock_preprompts,
        mock_agent_class,
        initial_prompt_content="Test prompt for clarification",
    ):
        p = tmp_path / self.project_path_string
        p.mkdir(parents=True, exist_ok=True)
        (p / "prompt").write_text(initial_prompt_content)

        # Configure PrepromptsHolder mock
        def get_preprompt_side_effect(key):
            if key == "clarify_initial_prompt":
                return DEFAULT_CLARIFY_INITIAL_PROMPT
            elif key == "clarify_next_step_or_ready":
                return DEFAULT_CLARIFY_NEXT_STEP_PROMPT
            elif key == "philosophy":
                return DEFAULT_PHILOSOPHY_PROMPT
            # Add other preprompts if main() tries to load them before clarification
            return f"Content for {key}" 
        mock_preprompts.return_value.get_preprompt.side_effect = get_preprompt_side_effect

        args = DefaultArgumentsMain(
            str(p),
            clarify_mode=True,
            model="gpt-4-test", # Using a distinct model for easier mocking if needed
            no_execution=True, # Important to prevent actual agent execution beyond init
        )
        
        # We expect typer.Exit for "quit"
        if "quit" in mock_input.side_effect if callable(mock_input.side_effect) else []:
             with pytest.raises(typer.Exit):
                args()
        else:
            args()
        
        return mock_agent_class.return_value # Return the mocked agent instance

    @patch("gpt_engineer.applications.cli.main.CliAgent")
    @patch("gpt_engineer.applications.cli.main.PrepromptsHolder")
    @patch("builtins.print")
    @patch("builtins.input")
    @patch("gpt_engineer.core.ai.AI.next") # Patching AI.next directly
    def test_basic_clarification_loop(
        self, mock_ai_next_method, mock_input, mock_print, mock_preprompts, mock_agent_class, tmp_path
    ):
        initial_prompt = "Build a web app."
        q1 = "What framework for the web app?"
        a1 = "React"
        q2 = "What about backend?"
        a2 = "Node.js"

        # Simulate AI responses
        # Using AIMessage from langchain.schema as that's what AI.next appends
        from langchain.schema import AIMessage, HumanMessage, SystemMessage
        mock_ai_next_method.side_effect = [
            # First call (initial prompt results in q1)
            [SystemMessage(content=DEFAULT_PHILOSOPHY_PROMPT), HumanMessage(content=initial_prompt), AIMessage(content=q1)],
            # Second call (after user answers a1, AI asks q2)
            [SystemMessage(content=DEFAULT_PHILOSOPHY_PROMPT), HumanMessage(content=initial_prompt), AIMessage(content=q1), HumanMessage(content=a1), AIMessage(content=q2)],
            # Third call (after user answers a2, AI is ready)
            [SystemMessage(content=DEFAULT_PHILOSOPHY_PROMPT), HumanMessage(content=initial_prompt), AIMessage(content=q1), HumanMessage(content=a1), AIMessage(content=q2), HumanMessage(content=a2), AIMessage(content="READY_TO_GENERATE")],
        ]
        mock_input.side_effect = [a1, a2]

        agent_instance = self._run_main_with_clarify_mocks(
            tmp_path, mock_ai_next_method, mock_input, mock_print, mock_preprompts, mock_agent_class, initial_prompt_content=initial_prompt
        )

        # Assertions for print (AI questions)
        # Note: print calls are many, check for specific AI questions
        printed_texts = " ".join(call_args[0][0] for call_args in mock_print.call_args_list if call_args[0])
        assert q1 in printed_texts
        assert q2 in printed_texts

        # Assert agent.init call
        agent_instance.init.assert_called_once()
        called_prompt_arg = agent_instance.init.call_args[0][0]
        assert isinstance(called_prompt_arg, Prompt)
        
        # Check that the final prompt text contains the dialogue
        assert f"Original Prompt:\n{initial_prompt}" in called_prompt_arg.text
        assert f"AI: {q1}" in called_prompt_arg.text
        assert f"User: {a1}" in called_prompt_arg.text
        assert f"AI: {q2}" in called_prompt_arg.text
        assert f"User: {a2}" in called_prompt_arg.text
        assert "READY_TO_GENERATE" in called_prompt_arg.text # The signal itself might be part of the last AI message

        # Assert code_gen_fn is gen_code
        assert agent_instance.code_gen_fn.__name__ == "gen_code"

    @patch("gpt_engineer.applications.cli.main.CliAgent")
    @patch("gpt_engineer.applications.cli.main.PrepromptsHolder")
    @patch("builtins.print")
    @patch("builtins.input")
    @patch("gpt_engineer.core.ai.AI.next")
    def test_done_keyword(
        self, mock_ai_next_method, mock_input, mock_print, mock_preprompts, mock_agent_class, tmp_path
    ):
        initial_prompt = "Build a calculator."
        q1 = "What operations?"
        
        from langchain.schema import AIMessage, HumanMessage, SystemMessage
        mock_ai_next_method.return_value = [ # Only one AI interaction needed
             SystemMessage(content=DEFAULT_PHILOSOPHY_PROMPT), HumanMessage(content=initial_prompt), AIMessage(content=q1)
        ]
        mock_input.return_value = "done" # User types 'done'

        agent_instance = self._run_main_with_clarify_mocks(
            tmp_path, mock_ai_next_method, mock_input, mock_print, mock_preprompts, mock_agent_class, initial_prompt_content=initial_prompt
        )
        
        printed_texts = " ".join(call_args[0][0] for call_args in mock_print.call_args_list if call_args[0])
        assert q1 in printed_texts # AI asks its question

        agent_instance.init.assert_called_once()
        called_prompt_arg = agent_instance.init.call_args[0][0]
        assert f"Original Prompt:\n{initial_prompt}" in called_prompt_arg.text
        assert f"AI: {q1}" in called_prompt_arg.text # Dialogue includes the AI's question
        # User's "done" is not added to history for prompt generation
        assert "User: done" not in called_prompt_arg.text 
        assert agent_instance.code_gen_fn.__name__ == "gen_code"

    @patch("gpt_engineer.applications.cli.main.CliAgent")
    @patch("gpt_engineer.applications.cli.main.PrepromptsHolder")
    @patch("builtins.print")
    @patch("builtins.input")
    @patch("gpt_engineer.core.ai.AI.next")
    def test_quit_keyword(
        self, mock_ai_next_method, mock_input, mock_print, mock_preprompts, mock_agent_class, tmp_path
    ):
        initial_prompt = "Build something."
        q1 = "Like what?"

        from langchain.schema import AIMessage, HumanMessage, SystemMessage
        mock_ai_next_method.return_value = [
            SystemMessage(content=DEFAULT_PHILOSOPHY_PROMPT), HumanMessage(content=initial_prompt), AIMessage(content=q1)
        ]
        mock_input.return_value = "quit"

        # Expect typer.Exit due to "quit"
        with pytest.raises(typer.Exit):
            self._run_main_with_clarify_mocks(
                tmp_path, mock_ai_next_method, mock_input, mock_print, mock_preprompts, mock_agent_class, initial_prompt_content=initial_prompt
            )
        
        agent_instance = mock_agent_class.return_value
        agent_instance.init.assert_not_called() # Agent init should not be called

    @patch("gpt_engineer.applications.cli.main.CliAgent")
    @patch("gpt_engineer.applications.cli.main.PrepromptsHolder")
    @patch("builtins.print")
    @patch("builtins.input")
    @patch("gpt_engineer.core.ai.AI.next")
    def test_max_turns_reached(
        self, mock_ai_next_method, mock_input, mock_print, mock_preprompts, mock_agent_class, tmp_path
    ):
        initial_prompt = "Complex app."
        max_turns = 7 # As defined in main.py
        
        from langchain.schema import AIMessage, HumanMessage, SystemMessage
        
        # AI always asks a question
        ai_responses = []
        current_history = [SystemMessage(content=DEFAULT_PHILOSOPHY_PROMPT), HumanMessage(content=initial_prompt)]
        for i in range(max_turns):
            question = f"Question {i+1}"
            current_history.append(AIMessage(content=question))
            ai_responses.append(list(current_history)) # AI.next returns the whole history + new AI message
            if i < max_turns -1: # For all but the last input
                 current_history.append(HumanMessage(content=f"Answer {i+1}"))


        mock_ai_next_method.side_effect = ai_responses
        mock_input.side_effect = [f"Answer {i+1}" for i in range(max_turns)]

        agent_instance = self._run_main_with_clarify_mocks(
            tmp_path, mock_ai_next_method, mock_input, mock_print, mock_preprompts, mock_agent_class, initial_prompt_content=initial_prompt
        )

        # Check that all questions were asked
        printed_texts = "".join(call_args[0][0] for call_args in mock_print.call_args_list if call_args[0])
        for i in range(max_turns):
            assert f"Question {i+1}" in printed_texts
        
        # Check that "Max clarification turns reached" was printed
        assert "Max clarification turns reached" in printed_texts

        agent_instance.init.assert_called_once()
        called_prompt_arg = agent_instance.init.call_args[0][0]
        assert f"Original Prompt:\n{initial_prompt}" in called_prompt_arg.text
        for i in range(max_turns):
            assert f"AI: Question {i+1}" in called_prompt_arg.text
            if i < max_turns: # All answers should be there
                 assert f"User: Answer {i+1}" in called_prompt_arg.text
        assert agent_instance.code_gen_fn.__name__ == "gen_code"

    @patch("gpt_engineer.applications.cli.main.CliAgent")
    @patch("gpt_engineer.applications.cli.main.PrepromptsHolder")
    @patch("builtins.print")
    @patch("builtins.input")
    @patch("gpt_engineer.core.ai.AI.next")
    def test_ai_ready_immediately(
        self, mock_ai_next_method, mock_input, mock_print, mock_preprompts, mock_agent_class, tmp_path
    ):
        initial_prompt = "Simple script."

        from langchain.schema import AIMessage, HumanMessage, SystemMessage
        mock_ai_next_method.return_value = [
            SystemMessage(content=DEFAULT_PHILOSOPHY_PROMPT), HumanMessage(content=initial_prompt), AIMessage(content="READY_TO_GENERATE")
        ]
        # mock_input should not be called

        agent_instance = self._run_main_with_clarify_mocks(
            tmp_path, mock_ai_next_method, mock_input, mock_print, mock_preprompts, mock_agent_class, initial_prompt_content=initial_prompt
        )
        
        mock_input.assert_not_called() # No user input needed

        # Check that "AI is ready to generate code." was printed
        printed_texts = "".join(call_args[0][0] for call_args in mock_print.call_args_list if call_args[0])
        assert "AI is ready to generate code." in printed_texts

        agent_instance.init.assert_called_once()
        called_prompt_arg = agent_instance.init.call_args[0][0]
        # Prompt should be the original prompt + the READY_TO_GENERATE signal as part of AI message
        assert f"Original Prompt:\n{initial_prompt}" in called_prompt_arg.text
        assert "READY_TO_GENERATE" in called_prompt_arg.text 
        # No clarification questions or answers should be in the prompt
        assert "AI: " not in called_prompt_arg.text.split("Clarification Dialogue:")[1].split("READY_TO_GENERATE")[0] # Check between dialogue start and ready signal
        assert agent_instance.code_gen_fn.__name__ == "gen_code"


class TestLoadPrompt:
    #  Load prompt from existing file in input_repo
    def test_load_prompt_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_repo = DiskMemory(tmp_dir)
            prompt_file = "prompt.txt"
            prompt_content = "This is the prompt"
            input_repo[prompt_file] = prompt_content

            improve_mode = False
            image_directory = ""

            result = load_prompt(input_repo, improve_mode, prompt_file, image_directory)

            assert isinstance(result, Prompt)
            assert result.text == prompt_content
            assert result.image_urls is None

    #  Prompt file does not exist in input_repo, and improve_mode is False
    def test_load_prompt_no_file_improve_mode_false(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_repo = DiskMemory(tmp_dir)
            prompt_file = "prompt.txt"

            improve_mode = False
            image_directory = ""

            with patch(
                "builtins.input",
                return_value="What application do you want gpt-engineer to generate?",
            ):
                result = load_prompt(
                    input_repo, improve_mode, prompt_file, image_directory
                )

            assert isinstance(result, Prompt)
            assert (
                result.text == "What application do you want gpt-engineer to generate?"
            )
            assert result.image_urls is None

    #  Prompt file is a directory
    def test_load_prompt_directory_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_repo = DiskMemory(tmp_dir)
            prompt_file = os.path.join(tmp_dir, "prompt")

            os.makedirs(os.path.join(tmp_dir, prompt_file))

            improve_mode = False
            image_directory = ""

            with pytest.raises(ValueError):
                load_prompt(input_repo, improve_mode, prompt_file, image_directory)

    #  Prompt file is empty
    def test_load_prompt_empty_file(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_repo = DiskMemory(tmp_dir)
            prompt_file = "prompt.txt"
            input_repo[prompt_file] = ""

            improve_mode = False
            image_directory = ""

            with patch(
                "builtins.input",
                return_value="What application do you want gpt-engineer to generate?",
            ):
                result = load_prompt(
                    input_repo, improve_mode, prompt_file, image_directory
                )

            assert isinstance(result, Prompt)
            assert (
                result.text == "What application do you want gpt-engineer to generate?"
            )
            assert result.image_urls is None

    #  image_directory does not exist in input_repo
    def test_load_prompt_no_image_directory(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            input_repo = DiskMemory(tmp_dir)
            prompt_file = "prompt.txt"
            prompt_content = "This is the prompt"
            input_repo[prompt_file] = prompt_content

            improve_mode = False
            image_directory = "tests/test_data"
            shutil.copytree(image_directory, os.path.join(tmp_dir, image_directory))

            result = load_prompt(input_repo, improve_mode, prompt_file, image_directory)

            assert isinstance(result, Prompt)
            assert result.text == prompt_content
            assert "mona_lisa.jpg" in result.image_urls


#     def test_log_creation_in_improve_mode(self, tmp_path, monkeypatch):
#         def improve_generator():
#             yield "y"
#             while True:
#                 yield "n"  # Subsequent responses
#
#         gen = improve_generator()
#         monkeypatch.setattr("builtins.input", lambda _: next(gen))
#         p = tmp_path / "projects/example"
#         p.mkdir(parents=True)
#         (p / "prompt").write_text(prompt_text)
#         (p / "main.py").write_text("The program will be written in this file")
#         meta_p = p / META_DATA_REL_PATH
#         meta_p.mkdir(parents=True)
#         (meta_p / "file_selection.toml").write_text(
#             """
#         [files]
#         "main.py" = "selected"
#                     """
#         )
#         os.environ["GPTE_TEST_MODE"] = "True"
#         simplified_main(str(p), "improve")
#         DiskExecutionEnv(path=p)
#         assert (
#             (p / f".gpteng/memory/{DEBUG_LOG_FILE}").read_text().strip()
#             == """UPLOADED FILES:
# ```
# File: main.py
# 1 The program will be written in this file
#
# ```
# PROMPT:
# Make a python program that writes 'hello' to a file called 'output.txt'
# CONSOLE OUTPUT:"""
#         )
#         del os.environ["GPTE_TEST_MODE"]
#
#     def test_log_creation_in_improve_mode_with_failing_diff(
#         self, tmp_path, monkeypatch
#     ):
#         def improve_generator():
#             yield "y"
#             while True:
#                 yield "n"  # Subsequent responses
#
#         def mock_salvage_correct_hunks(
#             messages: List, files_dict: FilesDict, error_message: List
#         ) -> FilesDict:
#             # create a falling diff
#             messages[
#                 -1
#             ].content = """To create a Python program that writes 'hello' to a file called 'output.txt', we will need to perform the following steps:
#
# 1. Open the file 'output.txt' in write mode.
# 2. Write the string 'hello' to the file.
# 3. Close the file to ensure the data is written and the file is not left open.
#
# Here is the implementation of the program in the `main.py` file:
#
# ```diff
# --- main.py
# +++ main.py
# @@ -0,0 +1,9 @@
# -create falling diff
# ```
#
# This concludes a fully working implementation."""
#             # Call the original function with modified messages or define your own logic
#             return salvage_correct_hunks(messages, files_dict, error_message)
#
#         gen = improve_generator()
#         monkeypatch.setattr("builtins.input", lambda _: next(gen))
#         monkeypatch.setattr(
#             "gpt_engineer.core.default.steps.salvage_correct_hunks",
#             mock_salvage_correct_hunks,
#         )
#         p = tmp_path / "projects/example"
#         p.mkdir(parents=True)
#         (p / "prompt").write_text(prompt_text)
#         (p / "main.py").write_text("The program will be written in this file")
#         meta_p = p / META_DATA_REL_PATH
#         meta_p.mkdir(parents=True)
#         (meta_p / "file_selection.toml").write_text(
#             """
#         [files]
#         "main.py" = "selected"
#                     """
#         )
#         os.environ["GPTE_TEST_MODE"] = "True"
#         simplified_main(str(p), "improve")
#         DiskExecutionEnv(path=p)
#         assert (
#             (p / f".gpteng/memory/{DEBUG_LOG_FILE}").read_text().strip()
#             == """UPLOADED FILES:
# ```
# File: main.py
# 1 The program will be written in this file
#
# ```
# PROMPT:
# Make a python program that writes 'hello' to a file called 'output.txt'
# CONSOLE OUTPUT:
# Invalid hunk: @@ -0,0 +1,9 @@
# -create falling diff
#
# Invalid hunk: @@ -0,0 +1,9 @@
# -create falling diff"""
#         )
#         del os.environ["GPTE_TEST_MODE"]
#
#     def test_log_creation_in_improve_mode_with_unexpected_exceptions(
#         self, tmp_path, monkeypatch
#     ):
#         def improve_generator():
#             yield "y"
#             while True:
#                 yield "n"  # Subsequent responses
#
#         def mock_salvage_correct_hunks(
#             messages: List, files_dict: FilesDict, error_message: List
#         ) -> FilesDict:
#             raise Exception("Mock exception in salvage_correct_hunks")
#
#         gen = improve_generator()
#         monkeypatch.setattr("builtins.input", lambda _: next(gen))
#         monkeypatch.setattr(
#             "gpt_engineer.core.default.steps.salvage_correct_hunks",
#             mock_salvage_correct_hunks,
#         )
#         p = tmp_path / "projects/example"
#         p.mkdir(parents=True)
#         (p / "prompt").write_text(prompt_text)
#         (p / "main.py").write_text("The program will be written in this file")
#         meta_p = p / META_DATA_REL_PATH
#         meta_p.mkdir(parents=True)
#         (meta_p / "file_selection.toml").write_text(
#             """
#         [files]
#         "main.py" = "selected"
#                     """
#         )
#         os.environ["GPTE_TEST_MODE"] = "True"
#         simplified_main(str(p), "improve")
#         DiskExecutionEnv(path=p)
#         assert (
#             (p / f".gpteng/memory/{DEBUG_LOG_FILE}").read_text().strip()
#             == """UPLOADED FILES:
# ```
# File: main.py
# 1 The program will be written in this file
#
# ```
# PROMPT:
# Make a python program that writes 'hello' to a file called 'output.txt'
# CONSOLE OUTPUT:
# Error while improving the project: Mock exception in salvage_correct_hunks"""
#         )
#         del os.environ["GPTE_TEST_MODE"]
