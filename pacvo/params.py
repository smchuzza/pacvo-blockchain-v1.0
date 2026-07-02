import os

COIN = 10**8
BLOCK_REWARD = 3 * COIN
STAKE_LOCK_BLOCKS = 128
TARGET_BLOCK_TIME = 1200
RETARGET_INTERVAL = 32
MAX_TARGET = 2**512 - 1
DEMO_TARGET = 2**500
_LAUNCH_TARGET = 2**486  # ~53k hashes/s → ~20 min expected block time at launch
# PACVO_DEMO=1 uses DEMO_TARGET for fast local demos (incompatible genesis chain).
INITIAL_TARGET = DEMO_TARGET if os.environ.get("PACVO_DEMO") == "1" else _LAUNCH_TARGET
GENESIS_TIMESTAMP = 1751452800


def stake_split(total_reward: int) -> tuple[int, int]:
    stake = total_reward // 10
    return (total_reward - stake, stake)
