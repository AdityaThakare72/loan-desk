"""Read-only access to the bank's CSV files. Loaded once at startup; tools look things up by applicant id."""
import pandas as pd

from app.config import DATA_DIR

applicants = pd.read_csv(DATA_DIR / "applicants.csv").set_index("applicant_id")
bureau = pd.read_csv(DATA_DIR / "credit_bureau.csv").set_index("applicant_id")
statements = pd.read_csv(DATA_DIR / "bank_statements.csv").set_index("applicant_id")
applications = pd.read_csv(DATA_DIR / "applications.csv").set_index("application_id")


def get_profile(applicant_id: str) -> dict | None:
    """Everything the bank knows about one applicant, or None if the id does not exist."""
    if applicant_id not in applicants.index:
        return None
    profile = {"applicant_id": applicant_id}
    profile.update(applicants.loc[applicant_id].to_dict())
    profile.update(bureau.loc[applicant_id].to_dict())
    profile.update(statements.loc[applicant_id].to_dict())
    # pandas gives numpy types; plain Python types are safe to put in JSON and in the graph state
    return {k: (v.item() if hasattr(v, "item") else ("" if pd.isna(v) else v)) for k, v in profile.items()}


def get_application_text(application_id: str) -> str | None:
    if application_id not in applications.index:
        return None
    return applications.loc[application_id, "application_text"]
