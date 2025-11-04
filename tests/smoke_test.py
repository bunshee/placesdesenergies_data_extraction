from src.models.schema import EnergyInvoiceRecord


def test_model_instantiation():
    rec = EnergyInvoiceRecord(
        supplier="EDF", energy_reference="25841823335979", energy_reference_type="PCE"
    )
    assert rec.energy_reference == "25841823335979"
