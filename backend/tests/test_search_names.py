"""Search finds a concept however its name is spaced, punctuated or slightly misspelt."""

import random
import string
import uuid


def found(client, query):
    response = client.get("/api/search", params={"q": query})
    assert response.status_code == 200
    return {concept["name"] for concept in response.json()["concepts"]}


def test_spacing_punctuation_and_small_typos_reach_the_concept(make_client, admin_client):
    suffix = "".join(random.choices(string.ascii_lowercase, k=6))
    name = f"DeepSeek{suffix}"
    assert admin_client.post("/api/admin/concepts", json={"name": name}).status_code == 200
    reader = make_client()
    for query in (f"deep seek {suffix}", f"Deep-Seek-{suffix}", f"what is deep.seek{suffix}?",
                  f"deepsek{suffix}", f"deepseke{suffix}"):  # spaced, punctuated, one letter missing or swapped
        assert found(reader, query) == {name}, query
    assert found(reader, f"leepseek{suffix}") == set()  # a misspelling keeps the first letter


def test_short_names_match_exactly_and_real_words_are_not_typos(make_client, admin_client):
    short = "Q" + "".join(random.choices(string.ascii_uppercase, k=2))
    long_name = "Halberd" + "".join(random.choices(string.ascii_lowercase, k=5))
    for name in (short, long_name):
        assert admin_client.post("/api/admin/concepts", json={"name": name}).status_code == 200
    reader = make_client()
    assert found(reader, short.lower()) == {short}
    assert found(reader, " ".join(short.lower())) == set()  # "q j x": too short to match loosely

    near = "Kalberd" + long_name[7:]  # one letter away, but a different first letter
    typo = long_name[:-1].lower()      # one letter missing
    word = long_name[:-1].lower() + "z"  # one letter different, and a word the team uses
    assert found(reader, typo) == {long_name}
    assert found(reader, near) == set()
    assert reader.post("/api/capture", data={"body": f"The {word} {uuid.uuid4().hex[:4]} rota is posted weekly."}
                       ).status_code == 200
    assert found(reader, word) == set()
