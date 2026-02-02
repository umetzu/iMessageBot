uv venv --python 3.14
source .venv/bin/activate

uv pip install requests
uv pip install python-dotenv
uv pip install aiohttp
uv run python imessage.py

#alternative withouth creating/activating env
#uv add requests
#uv run imessage.py