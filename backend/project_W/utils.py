from datetime import datetime, timedelta, timezone


def parse_version_tuple(version_tuple: tuple[int | str, ...]) -> tuple[int, int, int, int, bool]:
    assert len(version_tuple) >= 3

    is_dirty = False
    revisions = 0
    if len(version_tuple) >= 5:
        is_dirty = str(version_tuple[4]).find(".d") != -1
    if len(version_tuple) >= 4:
        revisions = int(str(version_tuple[3])[3:])

    return (
        int(version_tuple[0]),
        int(version_tuple[1]),
        int(version_tuple[2]),
        revisions,
        is_dirty,
    )


def minutes_from_now_to_datetime(minutes_from_now: int) -> datetime:
    expires_delta = timedelta(minutes=minutes_from_now)
    return datetime.now(timezone.utc) + expires_delta
