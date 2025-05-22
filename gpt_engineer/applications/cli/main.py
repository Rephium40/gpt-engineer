"""
Entrypoint for the CLI tool.

This module serves as the entry point for a command-line interface (CLI) tool.
It is designed to interact with OpenAI's language models.
The module provides functionality to:
- Load necessary environment variables,
- Configure various parameters for the AI interaction,
- Manage the generation or improvement of code projects.

Main Functionality
------------------
- Load environment variables required for OpenAI API interaction.
- Parse user-specified parameters for project configuration and AI behavior.
- Facilitate interaction with AI models, databases, and archival processes.

Parameters
----------
None

Notes
-----
- The `OPENAI_API_KEY` must be set in the environment or provided in a `.env` file within the working directory.
- The default project path is `projects/example`.
- When using the `azure_endpoint` parameter, provide the Azure OpenAI service endpoint URL.
"""

import difflib
import json
import logging
import os
import platform
import subprocess
import sys

from pathlib import Path

import openai
import typer

from dotenv import load_dotenv
from langchain.globals import set_llm_cache
from langchain_community.cache import SQLiteCache
from termcolor import colored

from gpt_engineer.applications.cli.cli_agent import CliAgent
from gpt_engineer.applications.cli.collect import collect_and_send_human_review
from gpt_engineer.applications.cli.file_selector import FileSelector
from gpt_engineer.core.ai import AI, ClipboardAI
from gpt_engineer.core.default.disk_execution_env import DiskExecutionEnv
from gpt_engineer.core.default.disk_memory import DiskMemory
from gpt_engineer.core.default.file_store import FileStore
from gpt_engineer.core.default.paths import PREPROMPTS_PATH, memory_path
from gpt_engineer.core.default.steps import (
    execute_entrypoint,
    gen_code,
    handle_improve_mode,
    improve_fn as improve_fn,
)
from gpt_engineer.core.files_dict import FilesDict
from gpt_engineer.core.git import stage_uncommitted_to_git
from gpt_engineer.core.preprompts_holder import PrepromptsHolder
from gpt_engineer.core.prompt import Prompt
from gpt_engineer.tools.custom_steps import (  # clarified_gen will likely be replaced by inline logic
    clarified_gen,
    lite_gen,
    self_heal,
)
from langchain.schema import HumanMessage, SystemMessage, AIMessage, BaseMessage


app = typer.Typer(
    context_settings={"help_option_names": ["-h", "--help"]}
)  # creates a CLI app


def load_env_if_needed():
    """
    Load environment variables if the OPENAI_API_KEY is not already set.

    This function checks if the OPENAI_API_KEY environment variable is set,
    and if not, it attempts to load it from a .env file in the current working
    directory. It then sets the openai.api_key for use in the application.
    """
    # We have all these checks for legacy reasons...
    if os.getenv("OPENAI_API_KEY") is None:
        load_dotenv()
    if os.getenv("OPENAI_API_KEY") is None:
        load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))

    openai.api_key = os.getenv("OPENAI_API_KEY")

    if os.getenv("ANTHROPIC_API_KEY") is None:
        load_dotenv()
    if os.getenv("ANTHROPIC_API_KEY") is None:
        load_dotenv(dotenv_path=os.path.join(os.getcwd(), ".env"))


def concatenate_paths(base_path, sub_path):
    # Compute the relative path from base_path to sub_path
    relative_path = os.path.relpath(sub_path, base_path)

    # If the relative path is not in the parent directory, use the original sub_path
    if not relative_path.startswith(".."):
        return sub_path

    # Otherwise, concatenate base_path and sub_path
    return os.path.normpath(os.path.join(base_path, sub_path))


def load_prompt(
    input_repo: DiskMemory,
    improve_mode: bool,
    prompt_file: str,
    image_directory: str,
    entrypoint_prompt_file: str = "",
) -> Prompt:
    """
    Load or request a prompt from the user based on the mode.

    Parameters
    ----------
    input_repo : DiskMemory
        The disk memory object where prompts and other data are stored.
    improve_mode : bool
        Flag indicating whether the application is in improve mode.

    Returns
    -------
    str
        The loaded or inputted prompt.
    """

    if os.path.isdir(prompt_file):
        raise ValueError(
            f"The path to the prompt, {prompt_file}, already exists as a directory. No prompt can be read from it. Please specify a prompt file using --prompt_file"
        )
    prompt_str = input_repo.get(prompt_file)
    if prompt_str:
        print(colored("Using prompt from file:", "green"), prompt_file)
        print(prompt_str)
    else:
        if not improve_mode:
            prompt_str = input(
                "\nWhat application do you want gpt-engineer to generate?\n"
            )
        else:
            prompt_str = input("\nHow do you want to improve the application?\n")

    if entrypoint_prompt_file == "":
        entrypoint_prompt = ""
    else:
        full_entrypoint_prompt_file = concatenate_paths(
            input_repo.path, entrypoint_prompt_file
        )
        if os.path.isfile(full_entrypoint_prompt_file):
            entrypoint_prompt = input_repo.get(full_entrypoint_prompt_file)

        else:
            raise ValueError("The provided file at --entrypoint-prompt does not exist")

    if image_directory == "":
        return Prompt(prompt_str, entrypoint_prompt=entrypoint_prompt)

    full_image_directory = concatenate_paths(input_repo.path, image_directory)
    if os.path.isdir(full_image_directory):
        if len(os.listdir(full_image_directory)) == 0:
            raise ValueError("The provided --image_directory is empty.")
        image_repo = DiskMemory(full_image_directory)
        return Prompt(
            prompt_str,
            image_repo.get(".").to_dict(),
            entrypoint_prompt=entrypoint_prompt,
        )
    else:
        raise ValueError("The provided --image_directory is not a directory.")


def get_preprompts_path(use_custom_preprompts: bool, input_path: Path) -> Path:
    """
    Get the path to the preprompts, using custom ones if specified.

    Parameters
    ----------
    use_custom_preprompts : bool
        Flag indicating whether to use custom preprompts.
    input_path : Path
        The path to the project directory.

    Returns
    -------
    Path
        The path to the directory containing the preprompts.
    """
    original_preprompts_path = PREPROMPTS_PATH
    if not use_custom_preprompts:
        return original_preprompts_path

    custom_preprompts_path = input_path / "preprompts"
    if not custom_preprompts_path.exists():
        custom_preprompts_path.mkdir()

    for file in original_preprompts_path.glob("*"):
        if not (custom_preprompts_path / file.name).exists():
            (custom_preprompts_path / file.name).write_text(file.read_text())
    return custom_preprompts_path


def compare(f1: FilesDict, f2: FilesDict):
    def colored_diff(s1, s2):
        lines1 = s1.splitlines()
        lines2 = s2.splitlines()

        diff = difflib.unified_diff(lines1, lines2, lineterm="")

        RED = "\033[38;5;202m"
        GREEN = "\033[92m"
        RESET = "\033[0m"

        colored_lines = []
        for line in diff:
            if line.startswith("+"):
                colored_lines.append(GREEN + line + RESET)
            elif line.startswith("-"):
                colored_lines.append(RED + line + RESET)
            else:
                colored_lines.append(line)

        return "\n".join(colored_lines)

    for file in sorted(set(f1) | set(f2)):
        diff = colored_diff(f1.get(file, ""), f2.get(file, ""))
        if diff:
            print(f"Changes to {file}:")
            print(diff)


def prompt_yesno() -> bool:
    TERM_CHOICES = colored("y", "green") + "/" + colored("n", "red") + " "
    while True:
        response = input(TERM_CHOICES).strip().lower()
        if response in ["y", "yes"]:
            return True
        if response in ["n", "no"]:
            break
        print("Please respond with 'y' or 'n'")


def get_system_info():
    system_info = {
        "os": platform.system(),
        "os_version": platform.version(),
        "architecture": platform.machine(),
        "python_version": sys.version,
        "packages": format_installed_packages(get_installed_packages()),
    }
    return system_info


def get_installed_packages():
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "list", "--format=json"],
            capture_output=True,
            text=True,
        )
        packages = json.loads(result.stdout)
        return {pkg["name"]: pkg["version"] for pkg in packages}
    except Exception as e:
        return str(e)


def format_installed_packages(packages):
    return "\n".join([f"{name}: {version}" for name, version in packages.items()])


@app.command(
    help="""
        GPT-engineer lets you:

        \b
        - Specify a software in natural language
        - Sit back and watch as an AI writes and executes the code
        - Ask the AI to implement improvements
    """
)
def main(
    project_path: str = typer.Argument(".", help="path"),
    model: str = typer.Option(
        os.environ.get("MODEL_NAME", "gpt-4o"), "--model", "-m", help="model id string"
    ),
    temperature: float = typer.Option(
        0.1,
        "--temperature",
        "-t",
        help="Controls randomness: lower values for more focused, deterministic outputs",
    ),
    improve_mode: bool = typer.Option(
        False,
        "--improve",
        "-i",
        help="Improve an existing project by modifying the files.",
    ),
    lite_mode: bool = typer.Option(
        False,
        "--lite",
        "-l",
        help="Lite mode: run a generation using only the main prompt.",
    ),
    clarify_mode: bool = typer.Option(
        False,
        "--clarify",
        "-c",
        help="Clarify mode - discuss specification with AI before implementation.",
    ),
    self_heal_mode: bool = typer.Option(
        False,
        "--self-heal",
        "-sh",
        help="Self-heal mode - fix the code by itself when it fails.",
    ),
    azure_endpoint: str = typer.Option(
        "",
        "--azure",
        "-a",
        help="""Endpoint for your Azure OpenAI Service (https://xx.openai.azure.com).
            In that case, the given model is the deployment name chosen in the Azure AI Studio.""",
    ),
    use_custom_preprompts: bool = typer.Option(
        False,
        "--use-custom-preprompts",
        help="""Use your project's custom preprompts instead of the default ones.
          Copies all original preprompts to the project's workspace if they don't exist there.""",
    ),
    llm_via_clipboard: bool = typer.Option(
        False,
        "--llm-via-clipboard",
        help="Use the clipboard to communicate with the AI.",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="Enable verbose logging for debugging."
    ),
    debug: bool = typer.Option(
        False, "--debug", "-d", help="Enable debug mode for debugging."
    ),
    prompt_file: str = typer.Option(
        "prompt",
        "--prompt_file",
        help="Relative path to a text file containing a prompt.",
    ),
    entrypoint_prompt_file: str = typer.Option(
        "",
        "--entrypoint_prompt",
        help="Relative path to a text file containing a file that specifies requirements for you entrypoint.",
    ),
    image_directory: str = typer.Option(
        "",
        "--image_directory",
        help="Relative path to a folder containing images.",
    ),
    use_cache: bool = typer.Option(
        False,
        "--use_cache",
        help="Speeds up computations and saves tokens when running the same prompt multiple times by caching the LLM response.",
    ),
    skip_file_selection: bool = typer.Option(
        False,
        "--skip-file-selection",
        "-s",
        help="Skip interactive file selection in improve mode and use the generated TOML file directly.",
    ),
    no_execution: bool = typer.Option(
        False,
        "--no_execution",
        help="Run setup but to not call LLM or write any code. For testing purposes.",
    ),
    sysinfo: bool = typer.Option(
        False,
        "--sysinfo",
        help="Output system information for debugging",
    ),
    diff_timeout: int = typer.Option(
        3,
        "--diff_timeout",
        help="Diff regexp timeout. Default: 3. Increase if regexp search timeouts.",
    ),
):
    """
    The main entry point for the CLI tool that generates or improves a project.

    This function sets up the CLI tool, loads environment variables, initializes
    the AI, and processes the user's request to generate or improve a project
    based on the provided arguments.

    Parameters
    ----------
    project_path : str
        The file path to the project directory.
    model : str
        The model ID string for the AI.
    temperature : float
        The temperature setting for the AI's responses.
    improve_mode : bool
        Flag indicating whether to improve an existing project.
    lite_mode : bool
        Flag indicating whether to run in lite mode.
    clarify_mode : bool
        Flag indicating whether to discuss specifications with AI before implementation.
    self_heal_mode : bool
        Flag indicating whether to enable self-healing mode.
    azure_endpoint : str
        The endpoint for Azure OpenAI services.
    use_custom_preprompts : bool
        Flag indicating whether to use custom preprompts.
    prompt_file : str
        Relative path to a text file containing a prompt.
    entrypoint_prompt_file: str
        Relative path to a text file containing a file that specifies requirements for you entrypoint.
    image_directory: str
        Relative path to a folder containing images.
    use_cache: bool
        Speeds up computations and saves tokens when running the same prompt multiple times by caching the LLM response.
    verbose : bool
        Flag indicating whether to enable verbose logging.
    skip_file_selection: bool
        Skip interactive file selection in improve mode and use the generated TOML file directly
    no_execution: bool
        Run setup but to not call LLM or write any code. For testing purposes.
    sysinfo: bool
        Flag indicating whether to output system information for debugging.

    Returns
    -------
    None
    """

    if debug:
        import pdb

        sys.excepthook = lambda *_: pdb.pm()

    if sysinfo:
        sys_info = get_system_info()
        for key, value in sys_info.items():
            print(f"{key}: {value}")
        raise typer.Exit()

    # Validate arguments
    if improve_mode and (clarify_mode or lite_mode):
        typer.echo("Error: Clarify and lite mode are not compatible with improve mode.")
        raise typer.Exit(code=1)

    # Set up logging
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO)
    if use_cache:
        set_llm_cache(SQLiteCache(database_path=".langchain.db"))
    if improve_mode:
        assert not (
            clarify_mode or lite_mode
        ), "Clarify and lite mode are not active for improve mode"

    load_env_if_needed()

    if llm_via_clipboard:
        ai = ClipboardAI()
    else:
        ai = AI(
            model_name=model,
            temperature=temperature,
            azure_endpoint=azure_endpoint,
        )

    path = Path(project_path)
    print("Running gpt-engineer in", path.absolute(), "\n")

    prompt = load_prompt(
        DiskMemory(path),
        improve_mode,
        prompt_file,
        image_directory,
        entrypoint_prompt_file,
    )

    # todo: if ai.vision is false and not llm_via_clipboard - ask if they would like to use gpt-4-vision-preview instead? If so recreate AI
    if not ai.vision:
        prompt.image_urls = None

    # configure generation function
    # if clarify_mode: # This will be handled inline now
    # code_gen_fn = clarified_gen
    if lite_mode:
        code_gen_fn = lite_gen
    else:
        code_gen_fn = gen_code

    # configure execution function
    if self_heal_mode:
        execution_fn = self_heal
    else:
        execution_fn = execute_entrypoint

    preprompts_holder = PrepromptsHolder(
        get_preprompts_path(use_custom_preprompts, Path(project_path))
    )

    memory = DiskMemory(memory_path(project_path))
    memory.archive_logs()

    execution_env = DiskExecutionEnv()
    agent = CliAgent.with_default_config(
        memory,
        execution_env,
        ai=ai,
        code_gen_fn=code_gen_fn,
        improve_fn=improve_fn,
        process_code_fn=execution_fn,
        preprompts_holder=preprompts_holder,
    )

    files = FileStore(project_path)
    if not no_execution:
        if improve_mode:
            files_dict_before, is_linting = FileSelector(project_path).ask_for_files(
                skip_file_selection=skip_file_selection
            )

            # lint the code
            if is_linting:
                files_dict_before = files.linting(files_dict_before)

            files_dict = handle_improve_mode(
                prompt, agent, memory, files_dict_before, diff_timeout=diff_timeout
            )
            if not files_dict or files_dict_before == files_dict:
                print(
                    f"No changes applied. Could you please upload the debug_log_file.txt in {memory.path}/logs folder in a github issue?"
                )

            else:
                print("\nChanges to be made:")
                compare(files_dict_before, files_dict)

                print()
                print(colored("Do you want to apply these changes?", "light_green"))
                if not prompt_yesno():
                    files_dict = files_dict_before

        else:
            if clarify_mode and not improve_mode:  # Ensure clarify_mode is not for --improve
                # Interactive clarification loop
                # For preprompts, using placeholders. These would need to be added to `preprompts/`
                # e.g., clarify_initial_prompt.txt, clarify_next_step_or_ready.txt
                # Fallback to hardcoded if not found, with a warning.
                try:
                    clarify_initial_prompt_text = preprompts_holder.get_preprompt(
                        "clarify_initial_prompt"
                    )
                except FileNotFoundError:
                    clarify_initial_prompt_text = (
                        "You are an AI assistant. The user has provided an initial prompt. "
                        "Review it. If it's clear and actionable for code generation, respond with the exact phrase 'READY_TO_GENERATE'. "
                        "Otherwise, ask a single, concise question to clarify the most critical ambiguity or missing piece of information. "
                        "Do not offer to write code yet."
                    )
                    print(
                        colored(
                            "Warning: Preprompt 'clarify_initial_prompt' not found. Using default.",
                            "yellow",
                        )
                    )

                try:
                    clarify_next_step_prompt_text = preprompts_holder.get_preprompt(
                        "clarify_next_step_or_ready"
                    )
                except FileNotFoundError:
                    clarify_next_step_prompt_text = (
                        "You are an AI assistant. You have been in a dialogue to clarify software requirements. "
                        "Review the entire conversation. If all critical ambiguities are resolved and you have "
                        "enough information to generate the code, respond with the exact phrase 'READY_TO_GENERATE'. "
                        "Otherwise, formulate the *next single most important* clarifying question to ask the user. "
                        "Do not offer to write code yet."
                    )
                    print(
                        colored(
                            "Warning: Preprompt 'clarify_next_step_or_ready' not found. Using default.",
                            "yellow",
                        )
                    )

                # Initialize conversation history
                # The agent.init() usually starts with a system message (philosophy) and the user prompt.
                # We'll build a similar list of messages here.
                initial_system_philosophy = preprompts_holder.get_preprompt("philosophy")
                conversation_history: list[BaseMessage] = [
                    SystemMessage(content=initial_system_philosophy),
                    HumanMessage(content=prompt.text),
                ]
                if prompt.image_urls: # Add image message if present
                    image_content = [{"type": "text", "text": "Initial prompt images:"}]
                    for url_data in prompt.image_urls:
                        # Assuming url_data is a dict like {'url': 'data:image/jpeg;base64,...'}
                        # and AI.next can handle this structure for vision models
                        image_content.append({"type": "image_url", "image_url": url_data})
                    conversation_history.append(HumanMessage(content=image_content))


                print(colored("Entering interactive clarification mode...", "cyan"))
                max_turns = 7
                current_turn = 0

                while current_turn < max_turns:
                    current_turn += 1
                    print(colored(f"\nClarification Turn {current_turn}/{max_turns}", "magenta"))

                    system_prompt_for_ai = (
                        clarify_initial_prompt_text
                        if current_turn == 1
                        else clarify_next_step_prompt_text
                    )

                    # Get AI's question or readiness signal
                    # The AI.next method was modified to accept system_prompt
                    updated_history_with_ai_response = ai.next(
                        messages=conversation_history,
                        system_prompt=system_prompt_for_ai,
                        step_name=f"clarification_turn_{current_turn}",
                    )
                    ai_response_message = updated_history_with_ai_response[-1]
                    
                    # Ensure content is string
                    if isinstance(ai_response_message.content, list):
                        # For vision models, content can be a list. We need the text part.
                        ai_response_text = ""
                        for part in ai_response_message.content:
                            if part.get("type") == "text":
                                ai_response_text = part.get("text", "")
                                break
                    else:
                        ai_response_text = str(ai_response_message.content)


                    conversation_history = updated_history_with_ai_response

                    if "READY_TO_GENERATE" in ai_response_text:
                        print(colored("AI is ready to generate code.", "green"))
                        break

                    print(colored("AI Assistant:", "blue"), ai_response_text)
                    
                    user_input = ""
                    try:
                        user_input = input(colored("Your answer (or 'done', 'quit'): ", "yellow")).strip()
                    except EOFError: # Handle Ctrl+D as quit
                        user_input = "quit"


                    if user_input.lower() == "done":
                        print(colored("Proceeding with generation based on current information.", "green"))
                        break
                    if user_input.lower() in ["quit", "exit"]:
                        print(colored("Exiting application.", "red"))
                        raise typer.Exit()

                    conversation_history.append(HumanMessage(content=user_input))

                    if current_turn == max_turns:
                        print(colored("Max clarification turns reached. Proceeding with generation.", "yellow"))

                # Serialize conversation history to a string for the Prompt object
                # This is a simple serialization. A more structured format might be better.
                final_prompt_text = f"Original Prompt:\n{prompt.text}\n\nClarification Dialogue:\n"
                for msg in conversation_history[1:]: # Skip initial system philosophy as agent.init will add it
                    if isinstance(msg, HumanMessage):
                        if isinstance(msg.content, list): # Handle image messages
                            text_parts = [part["text"] for part in msg.content if part["type"] == "text"]
                            final_prompt_text += f"User (with images): {' '.join(text_parts)}\n"
                        else:
                            final_prompt_text += f"User: {msg.content}\n"
                    elif isinstance(msg, AIMessage):
                        if isinstance(msg.content, list):
                             text_parts = [part["text"] for part in msg.content if part["type"] == "text"]
                             final_prompt_text += f"AI: {' '.join(text_parts)}\n"
                        else:
                            final_prompt_text += f"AI: {msg.content}\n"
                    elif isinstance(msg, SystemMessage) and msg.content != initial_system_philosophy : # Avoid duplicating the initial philosophy
                        final_prompt_text += f"System: {msg.content}\n"


                # Update the prompt object for agent.init()
                # Retain original image_urls and entrypoint_prompt if they exist
                prompt = Prompt(
                    text=final_prompt_text,
                    image_urls=prompt.image_urls, # Keep original images if any
                    entrypoint_prompt=prompt.entrypoint_prompt
                )
                # The code_gen_fn should be the standard gen_code, as clarification is done.
                agent.code_gen_fn = gen_code # Override if it was set to clarified_gen

            files_dict = agent.init(prompt)
            # collect user feedback if user consents
            # Note: config might need adjustment if clarified_gen is no longer used.
            # For now, assume gen_code is the primary generator after clarification.
            config_code_gen_fn_name = agent.code_gen_fn.__name__
            config = (config_code_gen_fn_name, execution_fn.__name__)
            collect_and_send_human_review(prompt, model, temperature, config, memory)

        stage_uncommitted_to_git(path, files_dict, improve_mode)

        files.push(files_dict)

    if ai.token_usage_log.is_openai_model():
        print("Total api cost: $ ", ai.token_usage_log.usage_cost())
    elif os.getenv("LOCAL_MODEL"):
        print("Total api cost: $ 0.0 since we are using local LLM.")
    else:
        print("Total tokens used: ", ai.token_usage_log.total_tokens())


if __name__ == "__main__":
    app()
