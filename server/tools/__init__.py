"""Small operator tools that talk to a *running* server over HTTP.

They exist because a measurement that lives in a browser tab (or in someone's
terminal history) is not a measurement. Nothing in here writes to the database,
opens a socket, or imports `main`: each tool is `GET` only, so it is safe to run
against a live install while the bot is trading paper.
"""
