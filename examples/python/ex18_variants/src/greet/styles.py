"""What a variant binds: plain functions, named by `module:attr` in the
manifest and loaded when the variant's graph is built."""


def formal(name: str) -> str:
    return f"Good day, {name.strip().title() or 'stranger'}."


def casual(name: str) -> str:
    return f"hey {name.strip().lower() or 'you'}!"
