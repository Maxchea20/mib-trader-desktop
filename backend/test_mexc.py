import asyncio
import websockets

async def test():
    print("Connecting to MEXC...")
    ws = await websockets.connect(
        "wss://contract.mexc.com/edge",
        open_timeout=12
    )
    print("MEXC CONNECTION OK")
    await ws.close()

asyncio.run(test())