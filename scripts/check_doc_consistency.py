#!/usr/bin/env python3
"""Check documentation consistency against code."""
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
ERRORS = []


def check_version_consistency():
    """Version must match in pyproject.toml and __init__.py."""
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text()
    init = (PROJECT_ROOT / "src" / "zion_terminal" / "__init__.py").read_text()
    
    m1 = re.search(r'version = "(.+?)"', pyproject)
    m2 = re.search(r'__version__ = "(.+?)"', init)
    
    if m1 and m2:
        if m1.group(1) != m2.group(1):
            ERRORS.append(f"Version mismatch: pyproject.toml={m1.group(1)}, __init__.py={m2.group(1)}")
        else:
            print(f"  ✓ Version: {m1.group(1)}")
    else:
        ERRORS.append("Could not parse version from pyproject.toml or __init__.py")


def check_config_variables():
    """Config variables in .env.example must match settings.py."""
    env_path = PROJECT_ROOT / ".env.example"
    settings_path = PROJECT_ROOT / "src" / "zion_terminal" / "config" / "settings.py"
    
    if not env_path.exists():
        ERRORS.append(".env.example not found")
        return
    
    env_vars = set(re.findall(r"^([A-Z_]+)=", env_path.read_text(), re.MULTILINE))
    settings_text = settings_path.read_text()
    
    for var in env_vars:
        if var not in settings_text:
            ERRORS.append(f"Config var {var} in .env.example but not in settings.py")
        else:
            print(f"  ✓ Config: {var}")


def check_cli_commands():
    """CLI commands in code must match README."""
    cli_path = PROJECT_ROOT / "src" / "zion_terminal" / "cli.py"
    readme_path = PROJECT_ROOT / "README.md"
    
    cli_text = cli_path.read_text()
    readme_text = readme_path.read_text()
    
    # Find all @main.command() decorated functions
    commands = re.findall(r'@main\.command\((?:"([^"]+)")?\)', cli_text)
    func_names = re.findall(r"def (\w+)\(ctx:", cli_text)
    
    all_commands = set()
    for c in commands:
        if c:
            all_commands.add(c)
    for f in func_names:
        if f != "main":
            all_commands.add(f.replace("_", "-"))
    
    for cmd in sorted(all_commands):
        if cmd in readme_text:
            print(f"  ✓ Command: {cmd}")
        else:
            ERRORS.append(f"Command '{cmd}' in cli.py but not in README")


def main():
    print("Checking documentation consistency...\n")
    
    print("Version:")
    check_version_consistency()
    
    print("\nConfig variables:")
    check_config_variables()
    
    print("\nCLI commands:")
    check_cli_commands()
    
    if ERRORS:
        print(f"\n❌ {len(ERRORS)} consistency error(s):")
        for e in ERRORS:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("\n✓ All consistency checks passed")
        sys.exit(0)


if __name__ == "__main__":
    main()
