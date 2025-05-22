# Interactive Clarification Flow for gpt-engineer

This document outlines the design for an interactive clarification flow within `gpt-engineer`. The goal is to allow the AI to ask follow-up questions when the initial prompt is ambiguous or lacks sufficient detail, leading to more accurate and useful code generation.

## 1. Overview

The interactive clarification flow will be triggered when `gpt-engineer` is run with a specific flag (e.g., `--interactive-clarify`) or potentially as a default step if ambiguity is detected with high confidence. Instead of proceeding directly to code generation or a predefined clarification step, the AI will enter a dialogue with the user to refine the requirements.

## 2. Core Components

### 2.1. Ambiguity Detection

*   **Initial Trigger:** The clarification process can be explicitly invoked by the user via a CLI flag (e.g., `gpte project_folder --interactive-clarify`).
*   **Implicit Trigger (Advanced):** The AI could be prompted to first assess the clarity and completeness of the user's initial prompt.
    *   A dedicated preliminary LLM call could be made with a system prompt like: "You are a requirements analyst. Review the following user prompt. Identify any ambiguities, missing critical information, or areas where more detail is needed for a software engineer to build the application. If the prompt is clear and sufficient, respond with 'CLEAR'. Otherwise, formulate a concise question to the user to clarify the most critical ambiguity. Frame your question to elicit specific information."
    *   If the response is not "CLEAR", the returned question initiates the interactive session.
    *   This requires careful prompt engineering to avoid overly chatty or unnecessary questions.

### 2.2. Question Generation by AI

*   **Prompting Strategy:** When ambiguity is detected (either implicitly or because the user is in `--interactive-clarify` mode and the AI is prompted to ask its first question), the AI will be prompted to generate a question for the user.
    *   **System Prompt for Clarification Question:** "You are an AI assistant helping a user specify software requirements. The user has provided an initial prompt. Your goal is to ask a single, clear, and concise question to resolve the most significant ambiguity or lack of detail that would prevent successful code generation. Based on the conversation so far and the initial prompt, what is the most important question you need to ask the user right now?"
    *   Optionally, the system prompt can guide the AI to suggest potential options if that makes sense for the ambiguity (e.g., "If appropriate, you can suggest 2-3 options the user can choose from.").

### 2.3. Types of Clarification Questions

The AI will be guided (via prompting) to ask questions that are:

1.  **Specific:** Targeting a particular piece of missing information.
2.  **Open-ended (but focused):** "Can you provide more details about the user authentication process?"
3.  **Yes/No (with context):** "Should the application support data persistence? If yes, do you have a preferred database type?"
4.  **Multiple-Choice (AI-suggested options):** "For user roles, are you thinking of (a) Admin/User, (b) Editor/Viewer, or (c) something else (please specify)?"

The AI should aim to ask one primary question at a time to keep the interaction manageable.

### 2.4. CLI Presentation of Questions

*   **Location:** The interaction will happen in `gpt_engineer/applications/cli/main.py`.
*   **Mechanism:**
    *   When the AI generates a question, it will be passed back to the CLI.
    *   The CLI will print the AI's question to the console, clearly indicating it's from the AI (e.g., `AI Assistant: [Question]`).
    *   Example:
        ```
        gpt-engineer: Initial prompt received.
        AI Assistant: "Regarding the user profiles, should users be able to upload a profile picture? (yes/no)"
        User:
        ```

### 2.5. User Input Capture

*   **Mechanism:**
    *   The CLI will use Python's `input()` function to capture the user's typed response.
    *   The user's response will be captured as a string.
    *   A clear prompt for input will be shown, e.g., `Your answer: `.
*   **Keywords for Control:** We can introduce keywords for users to manage the flow, e.g.:
    *   `done`: If the user believes they have provided enough clarification and wants the AI to proceed with generation.
    *   `quit` or `exit`: To terminate the clarification process and the program.

### 2.6. Refining Understanding & Loop

1.  The user's textual answer is captured.
2.  This answer is then appended to the ongoing conversation history with the AI.
3.  The AI is then prompted again:
    *   **System Prompt for Next Step:** "You are an AI assistant. You have asked a clarifying question, and the user has responded. Review the entire conversation, including the initial prompt and all Q&A.
        1.  If all critical ambiguities are resolved and you have enough information to generate the code, respond with the exact phrase `READY_TO_GENERATE`.
        2.  Otherwise, formulate the *next single most important* clarifying question to ask the user.
        3.  If you suggest options, keep them concise."
4.  **Loop or Proceed:**
    *   If the AI responds with `READY_TO_GENERATE`, the interactive loop terminates, and the system proceeds to the code generation phase, using the entire accumulated conversation (initial prompt + Q&A) as the basis.
    *   If the AI responds with another question, the CLI presents it, and the loop continues from step 2.4.
5.  **Maximum Turns:** To prevent infinite loops, a maximum number of clarification turns (e.g., 5-7) should be implemented. If the limit is reached, the AI can be forced to proceed with generation using the information gathered so far, or the user can be asked if they want to continue clarifying or proceed.

### 2.7. Prompting Strategy Changes

*   **Initial Clarification Prompt:** As described in 2.1, a prompt to assess the initial user request for clarity.
*   **Question Generation Prompt:** As described in 2.2, to ask the AI to formulate a question.
*   **Continuation/Resolution Prompt:** As described in 2.6, to ask the AI to evaluate if it's ready to generate or if it needs to ask another question.
*   The overall conversation history (original prompt + all clarification Q&A) must be maintained and passed to the LLM in each turn of the clarification loop. This allows the AI to build upon previous interactions.

## 3. Implementation Locus

*   **`gpt_engineer/applications/cli/main.py`:**
    *   Will handle the main loop for interactive Q&A.
    *   Presenting questions to the user via `print()`.
    *   Capturing user input via `input()`.
    *   Managing the conversation history list (of `Message` objects or similar).
    *   Calling the AI for the next question or to check if ready for generation.
    *   Handling control keywords (`done`, `quit`).
*   **`gpt_engineer/core/ai.py` (or a new dedicated clarification module/class):**
    *   The `AI` class will need methods to support the clarification dialogue. This might involve a new method like `ask_clarification_question(messages: List[Message], system_prompt_for_question: str) -> str` which returns the AI's question, and another method like `check_readiness_or_next_question(messages: List[Message], system_prompt_for_readiness: str) -> Union[str, Literal['READY_TO_GENERATE']]`.
    *   Alternatively, the existing `next()` method could be used, but the system prompts passed to it would be specific to the clarification phase.
*   **`gpt_engineer/core/default/steps.py` or `gpt_engineer/tools/custom_steps.py`:**
    *   A new step function (e.g., `interactive_clarification_step`) would orchestrate the process, calling the necessary AI methods and managing the state. This function would be invoked in `main.py` if the interactive mode is active. This step would accumulate the refined prompt/conversation.
    *   The output of this step (the fully clarified prompt/conversation) would then be passed to `gen_code` or a similar generation function.

## 4. Example Workflow (Simplified)

1.  User: `gpte my_project --interactive-clarify -p "Build a simple timer app"`
2.  `main.py`: Loads initial prompt "Build a simple timer app".
3.  `interactive_clarification_step`: Calls AI with initial prompt + "question generation" system prompt.
4.  AI: "Should the timer count up or count down?"
5.  `main.py`: Prints "AI Assistant: Should the timer count up or count down?"
6.  `main.py`: Captures user input: "Count down."
7.  `interactive_clarification_step`: Appends "User: Count down." to history. Calls AI with history + "readiness/next question" system prompt.
8.  AI: "What should happen when the timer reaches zero? (e.g., play a sound, show an alert, nothing)"
9.  `main.py`: Prints "AI Assistant: What should happen when the timer reaches zero? (e.g., play a sound, show an alert, nothing)"
10. `main.py`: Captures user input: "Play a sound and show an alert."
11. `interactive_clarification_step`: Appends user response. Calls AI with history + "readiness/next question" system prompt.
12. AI: `READY_TO_GENERATE`
13. `interactive_clarification_step`: Returns the full conversation history.
14. `main.py`: Proceeds to code generation using the enriched conversation.

## 5. Considerations

*   **Cost:** Each turn in the clarification dialogue is an additional LLM call, increasing cost and time. The process should be efficient.
*   **User Fatigue:** Too many questions can frustrate the user. The AI should be prompted to ask only essential questions.
*   **Prompt Engineering:** The quality of the system prompts for clarification will be crucial for the effectiveness of this feature.
*   **State Management:** The conversation history needs to be carefully managed and passed between the CLI and AI components.
*   **Fallback:** If clarification doesn't resolve effectively after max turns, have a graceful way to proceed or terminate.

This design provides a framework for implementing an interactive clarification flow. Further refinement of prompts and logic will be needed during implementation.
---
