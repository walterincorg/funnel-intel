"""
Local browser-use agent.

Usage:
    python agent.py                          # runs the default task
    python agent.py "your custom task here"  # runs a custom task
"""

import asyncio
import sys

from browser_use import Agent, Browser, BrowserProfile
from config import get_llm

DEFAULT_TASK = (
    "Go to google.com, search for 'browser-use AI agent python', "
    "and tell me the title and URL of the first organic result."
)


async def run(task: str) -> str:
    browser = Browser(
        browser_profile=BrowserProfile(
            headless=False,  # keep the window visible so you can watch
        )
    )

    agent = Agent(
        task=task,
        llm=get_llm(),
        browser=browser,
    )

    result = await agent.run()
    await browser.stop()
    return result


if __name__ == "__main__":
    task = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else DEFAULT_TASK
    print(f"\nTask: {task}\n")
    result = asyncio.run(run(task))
    print(f"\nResult:\n{result}")
