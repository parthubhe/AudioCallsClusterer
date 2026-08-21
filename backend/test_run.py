import asyncio
import traceback
from pipeline import CACHE_DIR, run_pipeline_from_cache

async def run():
    files = [p for p in CACHE_DIR.glob('*.wav')][:100]
    if not files:
        print('No files in cache')
        return
        
    try:
        print(f'Running with {len(files)} files')
        res = await run_pipeline_from_cache(files)
        print('Success')
    except Exception as e:
        traceback.print_exc()

asyncio.run(run())
