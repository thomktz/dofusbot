from dofus_brain.world.combat_execution import CombatExecutor
from dofus_brain.world.dialogue_execution import DialogueExecutor
from dofus_brain.world.interaction_execution import InteractiveExecutor
from dofus_brain.world.inventory_execution import InventoryExecutor
from dofus_brain.world.navigation_execution import NavigationExecutor
from dofus_brain.world.attack_execution import AttackExecutor
from dofus_brain.world.talk_execution import TalkExecutor


def test_executor_statuses_have_one_explicit_idle_shape():
    executors = [DialogueExecutor(), InteractiveExecutor(), NavigationExecutor(), CombatExecutor(), InventoryExecutor(), AttackExecutor(), TalkExecutor()]
    assert all(executor.status() == {"state": "idle"} for executor in executors)
