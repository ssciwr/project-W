import pytest
import schemathesis
from schemathesis.core.failures import Failure
from schemathesis.generation.case import Case
from schemathesis.openapi.checks import RejectedPositiveData, UndefinedStatusCode

common_schemathesis_config = {
    "checks": {
        "unsupported_method": {
            #this check reports that the allow header is missing if an unsupported method is being used, even though in my tests it was always present. Disable for now
            "enabled": False,
        },
        "ignored_auth": {
            #seems to be very buggy and unreliable in schemathesis. Constantly authenticated using a correct token while claiming it should have failed.
            "enabled": False,
        },
    },
    "output": {
        "sanitization": {
            # To show the token in the cURL snippet
            "enabled": False
        }
    }
}

@pytest.fixture
def not_authenticated_schema_fixture(backend, get_client):
    config = schemathesis.Config.from_dict(common_schemathesis_config)
    return schemathesis.openapi.from_url(
        f"{backend[0]}/openapi.json",
        verify=get_client[1],
        config=config,
    )

@pytest.fixture
def client_schema_fixture(backend, get_client, get_logged_in_client):
    client = get_logged_in_client(True)
    config = schemathesis.Config.from_dict({
        "headers": dict(client.headers),
        **common_schemathesis_config,
    })
    return schemathesis.openapi.from_url(
        f"{backend[0]}/openapi.json",
        verify=get_client[1],
        config=config,
    )

@pytest.fixture
def admin_schema_fixture(backend, get_client, get_logged_in_admin_client):
    client = get_logged_in_admin_client(True)
    config = schemathesis.Config.from_dict({
        "headers": dict(client.headers),
        **common_schemathesis_config,
    })
    return schemathesis.openapi.from_url(
        f"{backend[0]}/openapi.json",
        verify=get_client[1],
        config=config,
    )


#lazy schema loading
not_authenticated_schema = schemathesis.pytest.from_fixture("not_authenticated_schema_fixture")
client_schema = schemathesis.pytest.from_fixture("client_schema_fixture").exclude(tag_regex="(runners|admins)").exclude(path_regex="/api/jobs/events").exclude(path_regex="/api/users/(invalidate_token|invalidate_all_tokens|logout|delete)") #these paths can break future tests since each test category here shares one session
admin_schema = schemathesis.pytest.from_fixture("admin_schema_fixture").include(tag_regex="admins")


def case_handler(case: Case, get_client):
    response = case.call(verify=get_client[1])
    try:
        case.validate_response(response)
    #handle exception group:
    except* Failure as e:
        if response.status_code == 400 and response.text == '':
            #for really weird randomly generated data that some component (FastAPI, granian, python-multipart) cannot parse, the backend will just return code 400 with an empty response
            #schemathesis doesn't like that, because 400 is not documented, or an empty-response (non-json) is not documented
            pytest.skip("Expected: This is some really weird generated data")
        if type(e) == RejectedPositiveData and response.status_code == 422 and any(substring in response.text for substring in ["email", "password"]):
            # expected: email and password validation is too complicated to be represented in the OpenAPI schema. This will
            pytest.skip("Expected: Password/Email validation stricter than schema")
        if type(e) == UndefinedStatusCode and response.status_code == 400 and response.text == '{"detail":"There was an error parsing the body"}':
            # expected: FastAPI returns this if the body is not processable which can happen with randomly generated data
            pytest.skip("Expected: Unprocessable data")

@not_authenticated_schema.parametrize()
def test_schemathesis_unauthenticated(case: Case, get_client):
    case_handler(case, get_client)

@client_schema.parametrize()
def test_schemathesis_client(case: Case, get_client):
    case_handler(case, get_client)

@admin_schema.parametrize()
def test_schemathesis_admin(case: Case, get_client):
    case_handler(case, get_client)
