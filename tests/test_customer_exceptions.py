from app.exceptions.customer import CustomerAlreadyExistsError


def test_customer_already_exists_error_is_an_exception():
    error = CustomerAlreadyExistsError()

    assert isinstance(error, Exception)