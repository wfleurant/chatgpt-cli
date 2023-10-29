#!/bin/env python3

import atexit
import click
import datetime
import os
import requests
import sys
import yaml
import json
import re

from pathlib import Path
from prompt_toolkit import PromptSession, HTML
from prompt_toolkit.history import FileHistory
from rich.console import Console
from rich.markdown import Markdown
from rich.pretty import pprint

WORKDIR = Path(__file__).parent
CONFIG_FILE = Path(WORKDIR, "../config.yaml")
BASE_ENDPOINT = "https://api.openai.com/v1"
ENV_VAR = "OPENAI_API_KEY"
SAVE_FOLDER = "session-history"

file_path = os.path.abspath(__file__)
real_path = os.path.realpath(file_path)
WORKDIR = Path(real_path).parent

BASE_ENDPOINT = "https://api.openai.com/v1"
ENV_VAR = "OPENAI_API_KEY"

PRICING_RATE = {
    "gpt-3.5-turbo": {"prompt": 0.0015, "completion": 0.002},
    "gpt-3.5-turbo-0613": {"prompt": 0.0015, "completion": 0.002},
    "gpt-3.5-turbo-16k": {"prompt": 0.003, "completion": 0.004},
    "gpt-4": {"prompt": 0.03, "completion": 0.06},
    "gpt-4-0613": {"prompt": 0.03, "completion": 0.06},
    "gpt-4-32k": {"prompt": 0.06, "completion": 0.12},
    "gpt-4-32k-0613": {"prompt": 0.06, "completion": 0.12},
    "gpt-3.5-turbo-16k-0613": {"prompt": 0.003, "completion": 0.004},
}


# Initialize the messages history list
# It's mandatory to pass it at each API call in order to have a conversation
messages = []
# Initialize the token counters
prompt_tokens = 0
completion_tokens = 0
# Initialize the console
console = Console()


def load_config(config_file: str) -> dict:
    """
    Read a YAML config file and returns it's content as a dictionary
    """
    if not Path(config_file).exists():
        config_file.parent.mkdir(parents=True, exist_ok=True)
        with open(config_file, "w") as file:
            file.write(
                'api-key: "INSERT API KEY HERE"\n' + 'model: "gpt-3.5-turbo"\n'
                "temperature: 1\n"
                "#max_tokens: 500\n"
                "markdown: true\n"
            )
        console.print(f"New config file initialized: [green bold]{config_file}")

    with open(config_file) as file:
        config = yaml.load(file, Loader=yaml.FullLoader)

    return config


def load_history_data(history_file: str) -> dict:
    """
    Read a session history json file and return its content
    """
    with open(history_file) as file:
        content = json.loads(file.read())

    return content


def get_last_save_file() -> str:
    """
    Return the timestamp of the last saved session
    """
    files = [f for f in os.listdir(SAVE_FOLDER) if f.endswith(".json")]
    if files:
        ts = [f.replace("chatgpt-session-", "").replace(".json", "") for f in files]
        ts.sort()
        return ts[-1]
    return None


def create_save_folder() -> None:
    """
    Create the session history folder if not exists
    """
    if not os.path.exists(SAVE_FOLDER):
        os.mkdir(SAVE_FOLDER)


def add_markdown_system_message() -> None:
    """
    Try to force ChatGPT to always respond with well formatted code blocks and tables if markdown is enabled.
    """
    instruction = "Always use code blocks with the appropriate language tags. If asked for a table always format it using Markdown syntax."
    messages.append({"role": "system", "content": instruction})


def calculate_expense(
    prompt_tokens: int,
    completion_tokens: int,
    prompt_pricing: float,
    completion_pricing: float,
) -> float:
    """
    Calculate the expense, given the number of tokens and the pricing rates
    """
    expense = ((prompt_tokens / 1000) * prompt_pricing) + (
        (completion_tokens / 1000) * completion_pricing
    )

    # Format to display in decimal notation rounded to 6 decimals
    expense = "{:.6f}".format(round(expense, 6))

    return expense


def display_expense(model: str) -> None:
    """
    Given the model used, display total tokens used and estimated expense
    """
    total_expense = calculate_expense(
        prompt_tokens,
        completion_tokens,
        PRICING_RATE[model]["prompt"],
        PRICING_RATE[model]["completion"],
    )
    console.print(
        f"\n[green bold][{prompt_tokens + completion_tokens}] 🖕 ${total_expense}"
    )


def start_prompt(session: PromptSession, config: dict) -> None:
    """
    Ask the user for input, build the request and perform it
    """

    # TODO: Refactor to avoid a global variables
    global prompt_tokens, completion_tokens

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {config['api-key']}",
    }

    message = session.prompt(HTML(f"<b>[{prompt_tokens + completion_tokens}] >>> </b>"))

    if message.lower() == "/q":
        raise EOFError
    if message.lower() == "":
        raise KeyboardInterrupt

    messages.append({"role": "user", "content": message})

    # Base body parameters
    body = {
        "model": config["model"],
        "temperature": config["temperature"],
        "messages": messages,
    }
    # Optional parameter
    if "max_tokens" in config:
        body["max_tokens"] = config["max_tokens"]

    try:
        r = requests.post(
            f"{BASE_ENDPOINT}/chat/completions", headers=headers, json=body
        )
    except requests.ConnectionError:
        console.print("Connection error, try again...", style="red bold")
        messages.pop()
        raise KeyboardInterrupt
    except requests.Timeout:
        console.print("Connection timed out, try again...", style="red bold")
        messages.pop()
        raise KeyboardInterrupt

    if r.status_code == 200:
        response = r.json()

        message_response = response["choices"][0]["message"]
        usage_response = response["usage"]

        console.line()
        if config["markdown"]:
            console.print(Markdown(message_response["content"].strip()))
        else:
            console.print(message_response["content"].strip())
        console.line()

        # Update message history and token counters
        messages.append(message_response)
        prompt_tokens += usage_response["prompt_tokens"]
        completion_tokens += usage_response["completion_tokens"]

    elif r.status_code == 400:
        response = r.json()

        try:
            if "error" in response:
                try:
                    err_codeword, err_message = response["error"]["code"], response["error"]["message"]
                    raise KeyError
                except KeyError:
                    console.print(f"Invalid request please review API response:", style="bold red")
                    pprint(response)
                    raise EOFError
            else:
                raise AssertionError

        except AssertionError:
            console.print(f"Invalid request and could not find error details in API 404 response:", style="bold red")
            pprint(response)
            raise EOFError

        if err_codeword == "context_length_exceeded":
            try:
                m = r"This model's maximum context length is (?P<a>\d+) tokens.*?your messages resulted in (?P<b>\d+) tokens"
                re_ctx_msg = re.search(m, err_message).groupdict()

                ctx_maxlen, ctx_putlen, ctx_exceed = re_ctx_msg['a'], re_ctx_msg['b'], int(re_ctx_msg['b']) - int(re_ctx_msg['a'])
                console.print(f"Maximum context length ({ctx_maxlen}) exceeded. Try reducing {ctx_exceed} from the source total ({ctx_putlen})", style="red bold")
                raise EOFError

            except Exception as e:
                console.print("Maximum context length exceeded.", style="red bold")
                raise EOFError

    elif r.status_code == 401:
        console.print("Invalid API Key", style="bold red")
        raise EOFError

    elif r.status_code == 429:
        console.print("Rate limit or maximum monthly limit exceeded", style="bold red")
        messages.pop()
        raise KeyboardInterrupt
    
    elif r.status_code == 502 or r.status_code == 503:
        console.print("The server seems to be overloaded, try again", style="bold red")
        messages.pop()
        raise KeyboardInterrupt

    else:
        console.print(f"Unknown error, status code {r.status_code}", style="bold red")
        console.print(r.json())
        raise EOFError


@click.command()
@click.option(
    "-c",
    "--context",
    "context",
    type=click.File("r"),
    help="Path to a context file",
    multiple=True,
)
@click.option("-k", "--key", "api_key", help="Set the API Key")
@click.option("-m", "--model", "model", help="Set the model")
@click.option(
    "-ml", "--multiline", "multiline", is_flag=True,
    help="Use the multiline input mode"
)

def main(context, api_key, model, multiline) -> None:

    try:
        config = load_config(CONFIG_FILE)
    except FileNotFoundError:
        console.print("Configuration file not found", style="red bold")
        sys.exit(1)

    if multiline:
        pass
    else:
        multiline = config['multiline'] if 'multiline' in config else False

    session = PromptSession(multiline=multiline)

    # Order of precedence for API Key configuration:
    # Command line option > Environment variable > Configuration file

    # If the environment variable is set overwrite the configuration
    if os.environ.get(ENV_VAR):
        config["api-key"] = os.environ[ENV_VAR].strip()
    # If the --key command line argument is used overwrite the configuration
    if api_key:
        config["api-key"] = api_key.strip()
    # If the --model command line argument is used overwrite the configuration
    if model:
        config["model"] = model.strip()

    # Run the display expense function when exiting the script
    atexit.register(display_expense, model=config["model"])

    console.print(f"skynet: [green bold]activated")

    # Add the system message for code blocks in case markdown is enabled in the config file
    if config["markdown"]:
        add_markdown_system_message()

    # Context from the command line option
    if context:
        for c in context:
            console.print(f"Context file: [green bold]{c.name}")
            messages.append({"role": "system", "content": c.read().strip()})

    console.rule()

    while True:
        try:
            start_prompt(session, config)
        except KeyboardInterrupt:
            continue
        except EOFError:
            break


if __name__ == "__main__":
    main()
