from app.registry.discovery import auto_discover_skills
from app.registry.skill_registry import registry
from app.scheduler.intents import Intent

auto_discover_skills()
covered = set(registry.intents())
missing = [i.value for i in Intent if i.value not in covered]

print(f"总 Intent: {len(list(Intent))}")
print(f"已注册: {len(covered)}")
if missing:
    print(f"未注册: {missing}")
else:
    print("未注册: 无 ✓")