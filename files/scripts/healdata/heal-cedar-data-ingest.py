import argparse
import copy
import sys
import requests
import pydash
from uuid import UUID

# Defines how a field in metadata is going to be mapped into a key in filters
FILTER_FIELD_MAPPINGS = {
    "study_metadata.study_type.study_stage": "Study Type",
    "study_metadata.data.data_type": "Data Type",
    "study_metadata.study_type.study_subject_type": "Subject Type",
    "study_metadata.human_subject_applicability.gender_applicability": "Gender",
    "study_metadata.human_subject_applicability.age_applicability": "Age",
    "research_program": "Research Program",
}

# Defines how to handle special cases for values in filters
SPECIAL_VALUE_MAPPINGS = {
    "Interview/Focus Group - structured": "Interview/Focus Group",
    "Interview/Focus Group - semi-structured": "Interview/Focus Group",
    "Interview/Focus Group - unstructured": "Interview/Focus Group",
    "Questionnaire/Survey/Assessment - validated instrument": "Questionnaire/Survey/Assessment",
    "Questionnaire/Survey/Assessment - unvalidated instrument": "Questionnaire/Survey/Assessment",
    "Cis Male": "Male",
    "Cis Female": "Female",
    "Trans Male": "Transgender man/trans man/female-to-male (FTM)",
    "Female-to-male transsexual": "Transgender man/trans man/female-to-male (FTM)",
    "Trans Female": "Transgender woman/trans woman/male-to-female (MTF)",
    "Male-to-female transsexual": "Transgender woman/trans woman/male-to-female (MTF)",
    "Agender, Non-binary, gender non-conforming": "Genderqueer/gender nonconforming/neither exclusively male nor female",
    "Gender Queer": "Genderqueer/gender nonconforming/neither exclusively male nor female",
    "Intersex": "Genderqueer/gender nonconforming/neither exclusively male nor female",
    "Intersexed": "Genderqueer/gender nonconforming/neither exclusively male nor female",
    "Buisness Development": "Business Development",
}

# Defines field that we don't want to include in the filters
OMITTED_VALUES_MAPPING = {
    "study_metadata.human_subject_applicability.gender_applicability": "Not applicable"
}

# repository links
REPOSITORY_STUDY_ID_LINK_TEMPLATE = {
    "NIDDK Central": "https://repository.niddk.nih.gov/studies/<STUDY_ID>/",
    "NIDA Data Share": "https://datashare.nida.nih.gov/study/<STUDY_ID>",
    "NICHD DASH": "https://dash.nichd.nih.gov/study/<STUDY_ID>",
    "ICPSR": "https://www.icpsr.umich.edu/web/ICPSR/studies/<STUDY_ID>",
    "BioSystics-AP": "https://biosystics-ap.com/assays/assaystudy/<STUDY_ID>/",
}

CLINICAL_TRIALS_GOV_FIELDS = [
    "NCTId",
    "OfficialTitle",
    "BriefTitle",
    "Acronym",
    "StudyType",
    "OverallStatus",
    "StartDate",
    "StartDateType",
    "CompletionDate",
    "CompletionDateType",
    "IsFDARegulatedDrug",
    "IsFDARegulatedDevice",
    "IsPPSD",
    "BriefSummary",
    "DetailedDescription",
    "Condition",
    "DesignAllocation",
    "DesignPrimaryPurpose",
    "Phase",
    "DesignInterventionModel",
    "EnrollmentCount",
    "EnrollmentType",
    "DesignObservationalModel",
    "InterventionType",
    "PrimaryOutcomeMeasure",
    "SecondaryOutcomeMeasure",
    "OtherOutcomeMeasure",
    "Gender",
    "GenderBased",
    "MaximumAge",
    "MinimumAge",
    "IPDSharing",
    "IPDSharingTimeFrame",
    "IPDSharingAccessCriteria",
    "IPDSharingURL",
    "SeeAlsoLinkURL",
    "AvailIPDURL",
    "AvailIPDId",
    "AvailIPDComment",
    "PatientRegistry",
    "DesignTimePerspective",
]


def is_valid_uuid(uuid_to_test, version=4):
    """
    Check if uuid_to_test is a valid UUID.

     Parameters
    ----------
    uuid_to_test : str
    version : {1, 2, 3, 4}

     Returns
    -------
    `True` if uuid_to_test is a valid UUID, otherwise `False`.

    """

    try:
        uuid_obj = UUID(uuid_to_test, version=version)
    except ValueError:
        return False
    return str(uuid_obj) == uuid_to_test


def update_filter_metadata(metadata_to_update):
    # Retain these from existing filters
    save_filters = ["Common Data Elements"]
    filter_metadata = [
        filter
        for filter in metadata_to_update["advSearchFilters"]
        if filter["key"] in save_filters
    ]
    for metadata_field_key, filter_field_key in FILTER_FIELD_MAPPINGS.items():
        filter_field_values = pydash.get(metadata_to_update, metadata_field_key)
        if filter_field_values:
            if isinstance(filter_field_values, str):
                filter_field_values = [filter_field_values]
            if not isinstance(filter_field_values, list):
                print(filter_field_values)
                raise TypeError("Neither a string nor a list")
            for filter_field_value in filter_field_values:
                if (
                    metadata_field_key,
                    filter_field_value,
                ) in OMITTED_VALUES_MAPPING.items():
                    continue
                if filter_field_value in SPECIAL_VALUE_MAPPINGS:
                    filter_field_value = SPECIAL_VALUE_MAPPINGS[filter_field_value]
                filter_metadata.append(
                    {"key": filter_field_key, "value": filter_field_value}
                )
    filter_metadata = pydash.uniq(filter_metadata)
    metadata_to_update["advSearchFilters"] = filter_metadata
    # Retain these from existing tags
    save_tags = [
        "Data Repository",
        "Common Data Elements",
        "RequiredIDP",
        "Additional Acknowledgement",
    ]
    tags = [tag for tag in metadata_to_update["tags"] if tag["category"] in save_tags]
    # Add any new tags from advSearchFilters
    for f in metadata_to_update["advSearchFilters"]:
        if f["key"] == "Gender":
            continue
        tag = {"name": f["value"], "category": f["key"]}
        if tag not in tags:
            tags.append(tag)
    metadata_to_update["tags"] = tags
    return metadata_to_update


def get_client_token(client_id: str, client_secret: str):
    try:
        token_url = "http://fence-service/oauth2/token"
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        params = {"grant_type": "client_credentials"}
        data = "scope=openid user data"

        token_result = requests.post(
            token_url,
            params=params,
            headers=headers,
            data=data,
            auth=(client_id, client_secret),
        )
        token = token_result.json()["access_token"]
    except:
        raise Exception("Could not get token")
    return token


def get_related_studies(serial_num, guid, hostname):
    related_study_result = []

    if serial_num:
        mds = requests.get(
            f"http://metadata-service/metadata?nih_reporter.project_num_split.serial_num={serial_num}&data=true&limit=2000"
        )
        if mds.status_code == 200:
            related_study_metadata = mds.json()

            for (
                related_study_metadata_key,
                related_study_metadata_value,
            ) in related_study_metadata.items():
                if related_study_metadata_key == guid or (
                    related_study_metadata_value["_guid_type"] != "discovery_metadata"
                    and related_study_metadata_value["_guid_type"]
                    != "unregistered_discovery_metadata"
                ):
                    # do nothing for self, or for archived studies
                    continue
                title = (
                    related_study_metadata_value.get("gen3_discovery", {})
                    .get("study_metadata", {})
                    .get("minimal_info", {})
                    .get("study_name", "")
                )
                link = (
                    f"https://{hostname}/portal/discovery/{related_study_metadata_key}/"
                )
                related_study_result.append({"title": title, "link": link})
    return related_study_result


def get_clinical_trials_gov_metadata(nct_id):
    if not nct_id:
        return None
    ct_metadata = {}
    try:
        ct_metadata_result = requests.get(f"https://clinicaltrials.gov/api/v2/studies/{nct_id}?fields={'|'.join(CLINICAL_TRIALS_GOV_FIELDS)}")
        if ct_metadata_result.status_code != 200:
            raise Exception(f"Could not get clinicaltrials.gov metadata, error code {ct_metadata_result.status_code}")
        else:
            ct_metadata = ct_metadata_result.json()
    except Exception as exc:
        raise Exception(f"Could not get clinicaltrials.gov metadata: {exc}") from exc
    return ct_metadata


parser = argparse.ArgumentParser()

parser.add_argument("--directory", help="CEDAR Directory ID for registering ")
parser.add_argument("--cedar_client_id", help="The CEDAR client id")
parser.add_argument("--cedar_client_secret", help="The CEDAR client secret")
parser.add_argument("--hostname", help="Hostname")


args = parser.parse_args()

if not args.directory:
    print("Directory ID is required!")
    sys.exit(1)
if not args.cedar_client_id:
    print("CEDAR client id is required!")
    sys.exit(1)
if not args.cedar_client_secret:
    print("CEDAR client secret is required!")
    sys.exit(1)
if not args.hostname:
    print("Hostname is required!")
    sys.exit(1)

dir_id = args.directory
client_id = args.cedar_client_id
client_secret = args.cedar_client_secret
hostname = args.hostname

print("Getting CEDAR client access token")
access_token = get_client_token(client_id, client_secret)
token_header = {"Authorization": "bearer " + access_token}

limit = 10
offset = 0

# initialize this to be bigger than our initial call so we can go through while loop
total = 100

if not is_valid_uuid(dir_id):
    print("Directory ID is not in UUID format!")
    sys.exit(1)

while limit + offset <= total:
    # Get the metadata from cedar to register
    print("Querying CEDAR...")
    cedar = requests.get(
        f"http://cedar-wrapper-service/get-instance-by-directory/{dir_id}?limit={limit}&offset={offset}",
        headers=token_header,
    )

    # If we get metadata back now register with MDS
    if cedar.status_code == 200:
        metadata_return = cedar.json()
        if "metadata" not in metadata_return:
            print(
                "Got 200 from CEDAR wrapper but no metadata in body, something is not right!"
            )
            sys.exit(1)

        total = metadata_return["metadata"]["totalCount"]
        returned_records = len(metadata_return["metadata"]["records"])
        print(f"Successfully got {returned_records} record(s) from CEDAR directory")
        for cedar_record in metadata_return["metadata"]["records"]:
            # get the CEDAR instance id from cedar for querying in our MDS
            cedar_instance_id = pydash.get(
                cedar_record,
                "metadata_location.cedar_study_level_metadata_template_instance_ID",
            )
            if cedar_instance_id is None:
                print("This record doesn't have CEDAR instance id, skipping...")
                continue

            # Get the metadata record for the CEDAR instance id
            mds = requests.get(
                f"http://metadata-service/metadata?gen3_discovery.study_metadata.metadata_location.cedar_study_level_metadata_template_instance_ID={cedar_instance_id}&data=true"
            )
            if mds.status_code == 200:
                mds_res = mds.json()

                # the query result key is the record of the metadata. If it doesn't return anything then our query failed.
                if len(list(mds_res.keys())) == 0 or len(list(mds_res.keys())) > 1:
                    print(
                        f"Query returned nothing for template_instance_ID={cedar_instance_id}&data=true"
                    )
                    continue

                # get the key for our mds record
                mds_record_guid = list(mds_res.keys())[0]

                mds_res = mds_res[mds_record_guid]
                mds_cedar_register_data_body = {**mds_res}
                mds_discovery_data_body = {}
                if mds_res["_guid_type"] == "discovery_metadata":
                    print("Metadata is already registered. Updating MDS record")
                elif mds_res["_guid_type"] == "unregistered_discovery_metadata":
                    print(
                        "Metadata has not been registered. Registering it in MDS record"
                    )
                else:
                    print(
                        f"This metadata data record has a special GUID type \"{mds_res['_guid_type']}\" and will be skipped"
                    )
                    continue

                # some special handing for this field, because its parent will be deleted before we merging the CEDAR and MDS SLMD to avoid duplicated values
                cedar_record_other_study_websites = cedar_record.get(
                    "metadata_location", {}
                ).get("other_study_websites", [])
                # this ensures the nih_application_id, cedar_study_level_metadata_template_instance_ID and study_name are not alterable from CEDAR side
                del cedar_record["metadata_location"]
                cedar_record["minimal_info"]["study_name"] = (
                    mds_res["gen3_discovery"]["study_metadata"]
                    .get("minimal_info", {})
                    .get("study_name", "")
                )

                mds_res["gen3_discovery"]["study_metadata"].update(cedar_record)
                mds_res["gen3_discovery"]["study_metadata"]["metadata_location"][
                    "other_study_websites"
                ] = cedar_record_other_study_websites

                # setup citations
                doi_citation = mds_res["gen3_discovery"].get(
                    "doi_citation", ""
                )
                mds_res["gen3_discovery"]["study_metadata"]["citation"][
                    "heal_platform_citation"
                ] = doi_citation

                # setup repository_study_link
                data_repositories = (
                    mds_res.get("gen3_discovery", {})
                    .get("study_metadata", {})
                    .get("metadata_location", {})
                    .get("data_repositories", [])
                )
                repository_citation = "Users must also include a citation to the data as specified by the local repository."
                repository_citation_additional_text = ' The link to the study page at the local repository can be found in the "Data" tab.'
                for repository in data_repositories:
                    if (
                        repository["repository_name"]
                        and repository["repository_name"]
                        in REPOSITORY_STUDY_ID_LINK_TEMPLATE
                        and repository["repository_study_ID"]
                    ):
                        repository_study_link = REPOSITORY_STUDY_ID_LINK_TEMPLATE[
                            repository["repository_name"]
                        ].replace("<STUDY_ID>", repository["repository_study_ID"])
                        repository.update(
                            {"repository_study_link": repository_study_link}
                        )
                    if (repository.get("repository_study_link", None) and repository_citation_additional_text
                            not in repository_citation):
                        repository_citation += repository_citation_additional_text
                if len(data_repositories):
                    data_repositories[0] = {
                        **data_repositories[0],
                        "repository_citation": repository_citation,
                    }

                mds_res["gen3_discovery"]["study_metadata"]["metadata_location"][
                    "data_repositories"
                ] = copy.deepcopy(data_repositories)

                # set up related studies
                serial_num = None
                try:
                    serial_num = (
                        mds_res.get("nih_reporter", {})
                        .get("project_num_split", {})
                        .get("serial_num", None)
                    )
                except Exception:
                    print("Unable to get serial number for study")

                if serial_num is None:
                    print("Unable to get serial number for study")

                related_study_result = get_related_studies(
                    serial_num, mds_record_guid, hostname
                )
                mds_res["gen3_discovery"]["related_studies"] = copy.deepcopy(
                    related_study_result
                )

                # merge data from cedar that is not study level metadata into a level higher
                deleted_keys = []
                for key, value in mds_res["gen3_discovery"]["study_metadata"].items():
                    if not isinstance(value, dict):
                        mds_res["gen3_discovery"][key] = value
                        deleted_keys.append(key)
                for key in deleted_keys:
                    del mds_res["gen3_discovery"]["study_metadata"][key]

                mds_discovery_data_body = update_filter_metadata(
                    mds_res["gen3_discovery"]
                )

                clinical_trials_id = None
                try:
                    clinical_trials_id = (
                        mds_res["gen3_discovery"]["study_metadata"]
                            .get("metadata_location", {})
                            .get("clinical_trials_study_ID", "")
                    )
                except Exception:
                    print("Unable to get clinical_trials_study_ID for study")
                if clinical_trials_id:
                    try:
                        ct_gov_metadata = get_clinical_trials_gov_metadata(clinical_trials_id)
                        if ct_gov_metadata:
                            print(f"Got clinicaltrials.gov metadata for {mds_record_guid} with NCT ID {clinical_trials_id}")
                            mds_cedar_register_data_body["clinicaltrials_gov"] = copy.deepcopy(ct_gov_metadata)
                    except Exception as ex:
                        print(f'{ex}')
                # This means the old clinicaltrials_gov section is actually from CEDAR not clinicaltrials.gov, so remove it
                elif "clinicaltrials_gov" in mds_cedar_register_data_body:
                    del mds_cedar_register_data_body["clinicaltrials_gov"]

                mds_cedar_register_data_body["gen3_discovery"] = mds_discovery_data_body

                mds_cedar_register_data_body["_guid_type"] = "discovery_metadata"

                print(f"Metadata {mds_record_guid} is now being registered.")
                mds_put = requests.put(
                    f"http://metadata-service/metadata/{mds_record_guid}",
                    headers=token_header,
                    json=mds_cedar_register_data_body,
                )
                if mds_put.status_code == 200:
                    print(f"Successfully registered: {mds_record_guid}")
                else:
                    print(
                        f"Failed to register: {mds_record_guid}. Might not be MDS admin"
                    )
                    print(f"Status from MDS: {mds_put.status_code}")
            else:
                print(f"Failed to get information from MDS: {mds.status_code}")

    else:
        print(
            f"Failed to get information from CEDAR wrapper service: {cedar.status_code}"
        )

    if offset + limit == total:
        break

    offset = offset + limit
    if (offset + limit) > total:
        limit = total - offset

    if limit < 0:
        break
