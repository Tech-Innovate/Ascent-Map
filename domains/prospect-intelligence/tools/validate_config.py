from pathlib import Path
import json

import yaml

ROOT = Path(__file__).resolve().parents[1]


def fail(message):
    print(f"ERROR: {message}")
    raise SystemExit(1)


signals = yaml.safe_load((ROOT / "config" / "signals.yaml").read_text())
services = yaml.safe_load((ROOT / "config" / "services.yaml").read_text())
channels = yaml.safe_load((ROOT / "config" / "channels.yaml").read_text())
mapping = yaml.safe_load((ROOT / "config" / "maps-field-mapping.yaml").read_text())
json.loads((ROOT / "examples" / "profile.json").read_text())

signal_ids = {s["id"] for s in signals["signals"]}
if len(signal_ids) != len(signals["signals"]):
    fail("Duplicate signal IDs")

service_ids = {s["id"] for s in services["services"]}
if len(service_ids) != len(services["services"]):
    fail("Duplicate service IDs")

channel_ids = {c["id"] for c in channels["channels"]}
if len(channel_ids) != len(channels["channels"]):
    fail("Duplicate channel IDs")

referenced = set()


def collect_rule(rule):
    if not isinstance(rule, dict):
        return
    if "signal" in rule:
        referenced.add(rule["signal"])
    for child in rule.get("any", []) or []:
        collect_rule(child)
    for child in rule.get("all", []) or []:
        collect_rule(child)


for service in services["services"]:
    for key in ("prerequisites", "positive_rules", "limiting_rules"):
        for rule in service.get(key, []) or []:
            collect_rule(rule)

for channel in channels["channels"]:
    for key in ("positive_rules", "limiting_rules"):
        for rule in channel.get(key, []) or []:
            collect_rule(rule)

missing = sorted(referenced - signal_ids)
if missing:
    fail(f"Undefined signal references: {missing}")

required_mapping_fields = {
    "title",
    "category",
    "address",
    "review_count",
    "review_rating",
    "latitude",
}
if not required_mapping_fields.issubset(mapping["mappings"]):
    fail("Maps field mapping is missing one or more required core fields")

print("Configuration validation passed")
print(f"Signals:  {len(signal_ids)}")
print(f"Services: {len(service_ids)}")
print(f"Channels: {len(channel_ids)}")
print(f"Mappings: {len(mapping['mappings'])}")
