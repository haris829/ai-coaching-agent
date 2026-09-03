"""One minimal call to AWS Bedrock, to find out whether a key and profile work.

    python -m scripts.probe_bedrock

Exists because the coaching adapter we ship speaks the **Anthropic** Messages API at
``api.anthropic.com``, and Bedrock is a different service: a different host, a different auth scheme,
and the model named by an inference-profile ARN rather than a model id. Before writing a second
adapter it is worth knowing that the credential works at all.

Reads everything from the environment; nothing is hard-coded and nothing is written to disk::

    BEDROCK_REGION=us-east-1
    BEDROCK_API_KEY=...                 # the long-term Bedrock API key
    BEDROCK_MODEL_ID=arn:aws:bedrock:...application-inference-profile/...

Prints the reply, or the status code and the *shape* of the failure. It deliberately does not print
the response body on an auth failure: an AWS error can echo request detail, and this output tends to
get pasted into chats.
"""

from __future__ import annotations

import os
import sys
import urllib.parse

import httpx

REQUIRED = ("BEDROCK_REGION", "BEDROCK_API_KEY", "BEDROCK_MODEL_ID")


def main() -> int:
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        sys.exit(f"set these first: {', '.join(missing)}")

    region = os.environ["BEDROCK_REGION"]
    model = urllib.parse.quote(os.environ["BEDROCK_MODEL_ID"], safe="")
    url = f"https://bedrock-runtime.{region}.amazonaws.com/model/{model}/invoke"

    # Anthropic models on Bedrock take the Messages body, minus `model` (which is in the path) and
    # plus `anthropic_version`. That near-identity is what makes a second adapter cheap.
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 64,
        "system": "You are a coach. Reply with one short question and nothing else.",
        "messages": [{"role": "user", "content": "Say hello in one sentence."}],
    }

    print(f"POST https://bedrock-runtime.{region}.amazonaws.com/model/…/invoke")
    headers = {
        "Authorization": f"Bearer {os.environ['BEDROCK_API_KEY']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # httpx rather than urllib: urllib does not consult the Windows certificate store, so it fails
    # TLS verification against AWS on this machine. httpx ships certifi.
    try:
        response = httpx.post(url, json=body, headers=headers, timeout=60.0)
    except httpx.HTTPError as error:
        print(f"FAILED  could not reach Bedrock: {type(error).__name__}: {error}")
        return 1

    if response.status_code >= 400:
        # Status and message only. An AWS error body can quote the request back at us, and this
        # output tends to get pasted into chats.
        print(f"FAILED  HTTP {response.status_code}")
        try:
            print(f"  message: {str(response.json().get('message'))[:200]}")
        except Exception:  # noqa: BLE001 - a non-JSON error body is itself the answer
            print(f"  (non-JSON body, {len(response.content)} bytes)")
        return 1

    payload = response.json()

    text = "".join(
        block.get("text", "")
        for block in payload.get("content", [])
        if isinstance(block, dict) and block.get("type") == "text"
    )
    print("OK — the key and inference profile work")
    print(f"  model      : {payload.get('model')}")
    print(f"  stop_reason: {payload.get('stop_reason')}")
    print(f"  usage      : {payload.get('usage')}")
    print(f"  reply      : {text.strip()[:300]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
