"""EVM Execution Receipts and Event Logs."""

import json
from dataclasses import asdict, dataclass


@dataclass
class LogEntry:
    address: str
    topics: list[str]
    data: str  # hex string without 0x or with 0x
    log_index: int = 0

    def to_dict(self) -> dict:
        return {
            "address": self.address,
            "topics": list(self.topics),
            "data": self.data,
            "log_index": self.log_index,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LogEntry":
        return cls(
            address=d["address"],
            topics=list(d.get("topics", [])),
            data=d.get("data", ""),
            log_index=d.get("log_index", 0),
        )


@dataclass
class Receipt:
    tx_hash: str
    status: int  # 1 = success, 0 = revert / failure
    gas_used: int
    cumulative_gas_used: int
    contract_address: str | None = None
    logs: list[LogEntry] = None
    return_data: str = ""  # hex string

    def __post_init__(self) -> None:
        if self.logs is None:
            self.logs = []

    def to_dict(self) -> dict:
        return {
            "tx_hash": self.tx_hash,
            "status": self.status,
            "gas_used": self.gas_used,
            "cumulative_gas_used": self.cumulative_gas_used,
            "contract_address": self.contract_address,
            "logs": [log.to_dict() for log in self.logs],
            "return_data": self.return_data,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Receipt":
        return cls(
            tx_hash=d["tx_hash"],
            status=d["status"],
            gas_used=d["gas_used"],
            cumulative_gas_used=d["cumulative_gas_used"],
            contract_address=d.get("contract_address"),
            logs=[LogEntry.from_dict(log) for log in d.get("logs", [])],
            return_data=d.get("return_data", ""),
        )
