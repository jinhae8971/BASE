"""Specialist agents that produce AgentProposal objects."""
from .base import BaseAgent
from .macro_agent import MacroAgent
from .sector_agent import SectorAgent
from .value_agent import ValueAgent
from .quant_agent import QuantAgent
from .execution_agent import ExecutionAgent
from .reflection_agent import ReflectionAgent

__all__ = [
    "BaseAgent",
    "MacroAgent",
    "SectorAgent",
    "ValueAgent",
    "QuantAgent",
    "ExecutionAgent",
    "ReflectionAgent",
]
