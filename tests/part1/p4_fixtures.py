"""P4 fixtures: tables with a PLANTED role (junction / not / weak entity), neutral names.

Every FK targets a unique business-key column (`code`), never the surrogate `_id`, so P3's
value-based INDs find them. No table name below is a corpus name (plan 2.4).
"""

from __future__ import annotations

PAIR = ("student_ref", "course_ref")
# table -> planted role.  J = junction, N = not a junction, W = weak entity (proposed to the gate)
ROLES = {
    "enrolment": "J",  # key = {student_ref, course_ref}; payload `grade`; no surrogate id
    "booking": "J",  # same, plus a surrogate `_id` (FK pair is still a minimal unique combination)
    "touch": "N",  # ALL columns are FKs, but the pair repeats (exact duplicate rows): no key at all
    "card": "N",  # ALL columns are FKs, minimal key = {student_ref} alone, not the FK union
    "profile": "N",  # 1:1 extension: key = one FK set
    "room": "W",  # key = {building_ref, room_no}: FK + local discriminator
    "shift": "W",  # key = {building_ref, student_ref, day}: two FKs + local discriminator
}
TRUTH = {
    ("enrolment", "student_ref", "student", "code"),
    ("enrolment", "course_ref", "course", "code"),
    ("booking", "student_ref", "student", "code"),
    ("booking", "course_ref", "course", "code"),
    ("touch", "student_ref", "student", "code"),
    ("touch", "course_ref", "course", "code"),
    ("card", "student_ref", "student", "code"),
    ("card", "course_ref", "course", "code"),
    ("profile", "student_ref", "student", "code"),
    ("room", "building_ref", "building", "code"),
    ("shift", "building_ref", "building", "code"),
    ("shift", "student_ref", "student", "code"),
}


def rows(n: int) -> dict[str, dict[str, list]]:
    """n students (n >= 24); sizes vary so no constant can be tuned to one fixture."""
    m, k = max(n // 4, 6), max(n // 10, 4)
    s = [f"S{i:05d}" for i in range(n)]
    c = [f"C{i:04d}" for i in range(m)]
    b = [f"B{i:03d}" for i in range(k)]
    t = n // 3
    a_s, b_s, c_s = s[:t], s[t : 2 * t], s[2 * t :]  # disjoint slices: no accidental containment
    en = [(s[i], c[(i + j) % m], f"g{(i + j) % 5}") for i in range(n) for j in range(3)]
    bk = [(s[i], c[(i + j) % m], f"n{(i * j) % 7}") for i in range(n) for j in range(2)]
    touch = [(s[i], c[i % m]) for i in range(n) for _ in range(2)]
    rm = [(b[i], f"R{j}", f"cap{(i + j) % 6}") for i in range(k) for j in range(5)]
    sh = [(b[i], c_s[j % len(c_s)], f"D{d}") for i in range(k) for j in range(k) for d in range(3)]
    sh = list(dict.fromkeys(sh))  # (building, student, day) unique; (building, student) repeats
    return {
        "student": {
            "_id": [f"st{i}" for i in range(n)],
            "code": s,
            "label": [f"sl{i}" for i in range(n)],
        },
        "course": {
            "_id": [f"co{i}" for i in range(m)],
            "code": c,
            "label": [f"cl{i}" for i in range(m)],
        },
        "building": {
            "_id": [f"bu{i}" for i in range(k)],
            "code": b,
            "label": [f"bl{i}" for i in range(k)],
        },
        "enrolment": {
            "student_ref": [r[0] for r in en],
            "course_ref": [r[1] for r in en],
            "grade": [r[2] for r in en],
        },
        "booking": {
            "_id": [f"bk{i}" for i in range(len(bk))],
            "student_ref": [r[0] for r in bk],
            "course_ref": [r[1] for r in bk],
            "note": [r[2] for r in bk],
        },
        "touch": {"student_ref": [r[0] for r in touch], "course_ref": [r[1] for r in touch]},
        "card": {"student_ref": list(a_s), "course_ref": [c[i % m] for i in range(len(a_s))]},
        "profile": {"student_ref": list(b_s), "bio": [f"bio{i}" for i in range(len(b_s))]},
        "room": {
            "building_ref": [r[0] for r in rm],
            "room_no": [r[1] for r in rm],
            "capacity": [r[2] for r in rm],
        },
        "shift": {
            "building_ref": [r[0] for r in sh],
            "student_ref": [r[1] for r in sh],
            "day": [r[2] for r in sh],
        },
    }
