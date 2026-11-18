"""dofus_brain: perception, game state, planners, LLM client and WebSocket server for dofus-agent.

Design principle: the LLM only sets goals and reads fuzzy things (quest text, dialogs,
unknown screens). Deterministic code does perception, fight math, pathing and clicking.
"""

__version__ = "0.1.0"
PROTOCOL_VERSION = 1
