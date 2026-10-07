import asyncio
import base64
import json


async def async_read_json(path: str) -> dict:
    def _read():
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)

    return await asyncio.to_thread(_read)


async def async_write_json(path: str, data: dict):
    def _write():
        with open(path, "w", encoding="utf-8") as file:
            json.dump(data, file)

    await asyncio.to_thread(_write)


async def async_read_image_base64(path: str) -> str:
    def _read():
        with open(path, "rb") as file:
            content = file.read()
        return base64.b64encode(content).decode("utf-8")

    return await asyncio.to_thread(_read)