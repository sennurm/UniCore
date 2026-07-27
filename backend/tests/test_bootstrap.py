"""First-run bootstrap: university root + Super Admin, idempotent."""

from unicore import bootstrap


async def test_bootstrap_is_idempotent(db: None) -> None:
    first = await bootstrap.run("Test University", "UNI", "sadmin", "Super Admin")
    second = await bootstrap.run("Test University", "UNI", "sadmin", "Super Admin")
    assert first == second  # same root, same admin — nothing duplicated
