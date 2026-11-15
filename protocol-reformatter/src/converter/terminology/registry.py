"""Which terminology serves which protocol field."""

from converter.terminology import TermRegistry
from converter.terminology.ncit import NcitSource

SDTM_SYSTEM = "CDISC SDTM"
OTHER_SYSTEM = "other"  # no free download of the M11 system (MedDRA, WHO INN, EDQM) exists, so NCI Thesaurus codes stand in
DOSE_FORM_SUBSET = "CDISC SDTM Dosage Form Terminology"
ROUTE_SUBSET = "CDISC SDTM Route of Administration Terminology"
DRUG_TYPES = frozenset({"Pharmacologic Substance", "Immunologic Factor", "Biologically Active Substance",
                        "Organic Chemical", "Vitamin", "Hormone", "Antibiotic"})
CONDITION_TYPES = frozenset({"Disease or Syndrome", "Neoplastic Process", "Mental or Behavioral Dysfunction",
                             "Congenital Abnormality", "Injury or Poisoning", "Pathologic Function"})


def default_registry() -> TermRegistry:
    drugs = NcitSource(OTHER_SYSTEM, semantic_types=DRUG_TYPES)
    return TermRegistry({
        "dose_form": [NcitSource(SDTM_SYSTEM, subset=DOSE_FORM_SUBSET)],
        "route": [NcitSource(SDTM_SYSTEM, subset=ROUTE_SUBSET)],
        "control_inns": [drugs],
        "control_nonproprietary_names": [drugs],
        "population_diagnosis_or_condition": [NcitSource(OTHER_SYSTEM, semantic_types=CONDITION_TYPES)],
    })
