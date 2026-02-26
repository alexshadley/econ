from enum import Enum
from pydantic import BaseModel
from uuid import UUID


class Commodity(str, Enum):
    ORE = "ore"
    METAL = "metal"
    PARTS = "parts"
    CARS = "cars"


class FactoryType(str, Enum):
    METAL = "metal"  # ore -> metal
    PART = "part"  # metal -> parts
    CAR = "car"  # parts -> cars


# Mapping: factory type -> (input commodity, output commodity)
FACTORY_IO: dict[FactoryType, tuple[Commodity, Commodity]] = {
    FactoryType.METAL: (Commodity.ORE, Commodity.METAL),
    FactoryType.PART: (Commodity.METAL, Commodity.PARTS),
    FactoryType.CAR: (Commodity.PARTS, Commodity.CARS),
}


class ContractSide(str, Enum):
    BUY = "buy"  # sender wants to buy
    SELL = "sell"  # sender wants to sell


class Firm(BaseModel):
    id: str
    name: str
    cash: float
    inventory: dict[Commodity, int]
    factories: dict[FactoryType, int]
    running_factories: dict[FactoryType, int]


class Contract(BaseModel):
    id: UUID
    sender_id: str
    recipient_id: str
    commodity: Commodity
    quantity: int
    price_per_unit: float
    side: ContractSide
    status: str  # "pending", "accepted", "rejected"
    created_at: float


class Message(BaseModel):
    id: UUID
    sender_id: str
    recipient_id: str
    thread_id: str
    content: str
    timestamp: float
    read_by: set[str]

    class Config:
        arbitrary_types_allowed = True


class FactoryJob(BaseModel):
    id: UUID
    firm_id: str
    factory_type: FactoryType
    count: int
    started_at: float
    completes_at: float
