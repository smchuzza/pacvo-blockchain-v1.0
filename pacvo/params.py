COIN = 10**8
BLOCK_REWARD = 50 * COIN
STAKE_LOCK_BLOCKS = 128
TARGET_BLOCK_TIME = 1200
RETARGET_INTERVAL = 32
MAX_TARGET = 2**512 - 1
INITIAL_TARGET = 2**500
GENESIS_TIMESTAMP = 1751452800


def stake_split(total_reward: int) -> tuple[int, int]:
    stake = total_reward // 10
    return (total_reward - stake, stake)
