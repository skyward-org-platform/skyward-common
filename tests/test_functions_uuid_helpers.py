import uuid

import pytest

from skyward.functions import generate_job_id, generate_upload_id


def test_generate_job_id_returns_valid_uuid4_string():
    job_id = generate_job_id()
    parsed = uuid.UUID(job_id)
    assert parsed.version == 4
    assert str(parsed) == job_id


def test_generate_job_id_is_unique_per_call():
    assert generate_job_id() != generate_job_id()


def test_generate_upload_id_returns_valid_uuid4_string():
    upload_id = generate_upload_id()
    parsed = uuid.UUID(upload_id)
    assert parsed.version == 4
    assert str(parsed) == upload_id


def test_generate_upload_id_is_unique_per_call():
    assert generate_upload_id() != generate_upload_id()
