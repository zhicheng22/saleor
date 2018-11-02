import json
import re
from unittest.mock import Mock, patch

import pytest

import graphene
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.shortcuts import reverse
from saleor.account.models import Address, User
from saleor.graphql.account.mutations import (
    CustomerDelete, SetPassword, StaffDelete, StaffUpdate, UserDelete)
from tests.api.utils import get_graphql_content

from .utils import (
    assert_no_permission, assert_read_only_mode,
    convert_dict_keys_to_camel_case)


def test_create_token_mutation(admin_client, staff_user):
    query = """
    mutation TokenCreate($email: String!, $password: String!) {
        tokenCreate(email: $email, password: $password) {
            token
            errors {
                field
                message
            }
        }
    }
    """
    variables = json.dumps({'email': staff_user.email, 'password': 'password'})
    response = admin_client.post(
        reverse('api'), json.dumps({'query': query, 'variables': variables}),
        content_type='application/json')
    content = get_graphql_content(response)
    token_data = content['data']['tokenCreate']
    assert token_data['token']
    assert not token_data['errors']

    incorrect_variables = json.dumps(
        {'email': staff_user.email, 'password': 'incorrect'})
    response = admin_client.post(
        reverse('api'),
        json.dumps({'query': query, 'variables': incorrect_variables}),
        content_type='application/json')
    content = get_graphql_content(response)
    token_data = content['data']['tokenCreate']
    errors = token_data['errors']
    assert errors
    assert not errors[0]['field']
    assert not token_data['token']


def test_token_create_user_data(
        permission_manage_orders, staff_api_client, staff_user):
    query = """
    mutation TokenCreate($email: String!, $password: String!) {
        tokenCreate(email: $email, password: $password) {
            user {
                id
                email
                permissions {
                    code
                    name
                }
            }
        }
    }
    """

    permission = permission_manage_orders
    staff_user.user_permissions.add(permission)
    name = permission.name
    user_id = graphene.Node.to_global_id('User', staff_user.id)

    variables = {'email': staff_user.email, 'password': 'password'}
    response = staff_api_client.post_graphql(query, variables)
    content = get_graphql_content(response)
    token_data = content['data']['tokenCreate']
    assert token_data['user']['id'] == user_id
    assert token_data['user']['email'] == staff_user.email
    assert token_data['user']['permissions'][0]['name'] == name
    assert token_data['user']['permissions'][0]['code'] == 'MANAGE_ORDERS'


def test_query_user(staff_api_client, customer_user, permission_manage_users):
    user = customer_user
    query = """
    query User($id: ID!) {
        user(id: $id) {
            email
            isStaff
            isActive
            addresses {
                totalCount
            }
            orders {
                totalCount
            }
            dateJoined
            lastLogin
            defaultShippingAddress {
                firstName
                lastName
                companyName
                streetAddress1
                streetAddress2
                city
                cityArea
                postalCode
                countryArea
                phone
                country {
                    code
                }
            }
        }
    }
    """
    ID = graphene.Node.to_global_id('User', customer_user.id)
    variables = {'id': ID}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_users])
    content = get_graphql_content(response)
    data = content['data']['user']
    assert data['email'] == user.email
    assert data['isStaff'] == user.is_staff
    assert data['isActive'] == user.is_active
    assert data['addresses']['totalCount'] == user.addresses.count()
    assert data['orders']['totalCount'] == user.orders.count()
    address = data['defaultShippingAddress']
    user_address = user.default_shipping_address
    assert address['firstName'] == user_address.first_name
    assert address['lastName'] == user_address.last_name
    assert address['companyName'] == user_address.company_name
    assert address['streetAddress1'] == user_address.street_address_1
    assert address['streetAddress2'] == user_address.street_address_2
    assert address['city'] == user_address.city
    assert address['cityArea'] == user_address.city_area
    assert address['postalCode'] == user_address.postal_code
    assert address['country']['code'] == user_address.country.code
    assert address['countryArea'] == user_address.country_area
    assert address['phone'] == user_address.phone.as_e164


def test_query_customers(
        staff_api_client, user_api_client, permission_manage_users):
    query = """
    query Users {
        customers {
            totalCount
            edges {
                node {
                    isStaff
                }
            }
        }
    }
    """
    variables = {}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_users])
    content = get_graphql_content(response)
    users = content['data']['customers']['edges']
    assert users
    assert all([not user['node']['isStaff'] for user in users])

    # check permissions
    response = user_api_client.post_graphql(query, variables)
    assert_no_permission(response)


def test_query_staff(
        staff_api_client, user_api_client, staff_user, customer_user,
        admin_user, permission_manage_staff):
    query = """
    {
        staffUsers {
            edges {
                node {
                    email
                    isStaff
                }
            }
        }
    }
    """
    variables = {}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_staff])
    content = get_graphql_content(response)
    data = content['data']['staffUsers']['edges']
    assert len(data) == 2
    staff_emails = [user['node']['email'] for user in data]
    assert sorted(staff_emails) == [admin_user.email, staff_user.email]
    assert all([user['node']['isStaff'] for user in data])

    # check permissions
    response = user_api_client.post_graphql(query, variables)
    assert_no_permission(response)


def test_who_can_see_user(
        staff_user, customer_user, staff_api_client, user_api_client,
        permission_manage_users):
    query = """
    query User($id: ID!) {
        user(id: $id) {
            email
        }
    }
    """

    query_2 = """
    query Users {
        customers {
            totalCount
        }
    }
    """

    # Random person (even staff) can't see users data without permissions
    ID = graphene.Node.to_global_id('User', customer_user.id)
    variables = {'id': ID}
    response = staff_api_client.post_graphql(query, variables)
    assert_no_permission(response)

    response = staff_api_client.post_graphql(query_2)
    assert_no_permission(response)

    # Add permission and ensure staff can see user(s)
    staff_user.user_permissions.add(permission_manage_users)
    response = staff_api_client.post_graphql(query, variables)
    content = get_graphql_content(response)
    assert content['data']['user']['email'] == customer_user.email

    response = staff_api_client.post_graphql(query_2)
    content = get_graphql_content(response)
    assert content['data']['customers']['totalCount'] == 1


def test_customer_register(user_api_client):
    query = """
        mutation RegisterCustomer($password: String!, $email: String!) {
            customerRegister(input: {password: $password, email: $email}) {
                errors {
                    field
                    message
                }
                user {
                    id
                }
            }
        }
    """
    email = 'customer@example.com'
    variables = {'email': email, 'password': 'Password'}
    response = user_api_client.post_graphql(query, variables)
    assert_read_only_mode(response)


# @patch('saleor.account.emails.send_password_reset_email.delay')
def test_customer_create(staff_api_client, address, permission_manage_users):
    query = """
    mutation CreateCustomer(
        $email: String, $note: String, $billing: AddressInput,
        $shipping: AddressInput, $send_mail: Boolean) {
        customerCreate(input: {
            email: $email,
            note: $note,
            defaultShippingAddress: $shipping,
            defaultBillingAddress: $billing
            sendPasswordEmail: $send_mail
        }) {
            errors {
                field
                message
            }
            user {
                id
                defaultBillingAddress {
                    id
                }
                defaultShippingAddress {
                    id
                }
                email
                isActive
                isStaff
                note
            }
        }
    }
    """
    email = 'api_user@example.com'
    note = 'Test user'
    address_data = convert_dict_keys_to_camel_case(address.as_data())

    variables = {
        'email': email, 'note': note, 'shipping': address_data,
        'billing': address_data, 'send_mail': True}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_users])
    assert_read_only_mode(response)


def test_customer_update(
        staff_api_client, customer_user, address, permission_manage_users):
    query = """
    mutation UpdateCustomer($id: ID!, $isActive: Boolean, $note: String, $billing: AddressInput, $shipping: AddressInput) {
        customerUpdate(id: $id, input: {
            isActive: $isActive,
            note: $note,
            defaultBillingAddress: $billing
            defaultShippingAddress: $shipping
        }) {
            errors {
                field
                message
            }
            user {
                id
                defaultBillingAddress {
                    id
                }
                defaultShippingAddress {
                    id
                }
                isActive
                note
            }
        }
    }
    """

    # this test requires addresses to be set and checks whether new address
    # instances weren't created, but the existing ones got updated
    assert customer_user.default_billing_address
    assert customer_user.default_shipping_address
    billing_address_pk = customer_user.default_billing_address.pk
    shipping_address_pk = customer_user.default_shipping_address.pk

    id = graphene.Node.to_global_id('User', customer_user.id)
    note = 'Test update note'
    address_data = convert_dict_keys_to_camel_case(address.as_data())

    new_street_address = 'Updated street address'
    address_data['streetAddress1'] = new_street_address

    variables = {
        'id': id, 'isActive': False, 'note': note, 'billing': address_data,
        'shipping': address_data}

    # check unauthorized access
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_users])
    assert_read_only_mode(response)


def test_customer_delete(staff_api_client, customer_user, permission_manage_users):
    query = """
    mutation CustomerDelete($id: ID!) {
        customerDelete(id: $id){
            errors {
                field
                message
            }
            user {
                id
            }
        }
    }
    """
    customer_id = graphene.Node.to_global_id('User', customer_user.pk)
    variables = {'id': customer_id}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_users])
    assert_read_only_mode(response)


def test_customer_delete_errors(customer_user, admin_user, staff_user):
    info = Mock(context=Mock(user=admin_user))
    errors = CustomerDelete.clean_instance(info, staff_user, [])
    assert errors[0].field == 'id'
    assert errors[0].message == 'Cannot delete a staff account.'

    errors = CustomerDelete.clean_instance(info, customer_user, [])
    assert errors == []


def test_staff_create(
        staff_api_client, permission_manage_staff, permission_manage_products):
    query = """
    mutation CreateStaff($email: String, $permissions: [String], $send_mail: Boolean) {
        staffCreate(input: {email: $email, permissions: $permissions, sendPasswordEmail: $send_mail}) {
            errors {
                field
                message
            }
            user {
                id
                email
                isStaff
                isActive
                permissions {
                    code
                }
            }
        }
    }
    """

    permission_manage_products_codename = '%s.%s' % (
        permission_manage_products.content_type.app_label,
        permission_manage_products.codename)

    email = 'api_user@example.com'
    variables = {
        'email': email, 'permissions': [permission_manage_products_codename],
        'send_mail': True}

    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_staff])
    assert_read_only_mode(response)


def test_staff_update(staff_api_client, permission_manage_staff):
    query = """
    mutation UpdateStaff(
            $id: ID!, $permissions: [String], $is_active: Boolean) {
        staffUpdate(
                id: $id,
                input: {permissions: $permissions, isActive: $is_active}) {
            errors {
                field
                message
            }
            user {
                permissions {
                    code
                }
                isActive
            }
        }
    }
    """
    staff_user = User.objects.create(
        email='staffuser@example.com', is_staff=True)
    id = graphene.Node.to_global_id('User', staff_user.id)
    variables = {'id': id, 'permissions': []}

    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_staff])
    assert_read_only_mode(response)


def test_user_delete_errors(staff_user, customer_user, admin_user):
    info = Mock(context=Mock(user=staff_user))
    errors = UserDelete.clean_instance(info, staff_user, [])
    assert errors[0].field == 'id'
    assert errors[0].message == 'You cannot delete your own account.'

    info = Mock(context=Mock(user=staff_user))
    errors = UserDelete.clean_instance(info, admin_user, [])
    assert errors[0].field == 'id'
    assert errors[0].message == 'Only superuser can delete his own account.'


def test_staff_delete_errors(staff_user, customer_user, admin_user):
    info = Mock(context=Mock(user=staff_user))
    errors = StaffDelete.clean_instance(info, customer_user, [])
    assert errors[0].field == 'id'
    assert errors[0].message == 'Cannot delete a non-staff user.'

    info = Mock(context=Mock(user=admin_user))
    errors = StaffDelete.clean_instance(info, staff_user, [])
    assert not errors


def test_staff_update_errors(staff_user, customer_user, admin_user):
    errors = StaffUpdate.clean_is_active(None, customer_user, staff_user, [])
    assert not errors

    errors = StaffUpdate.clean_is_active(False, staff_user, staff_user, [])
    assert errors[0].field == 'isActive'
    assert errors[0].message == 'Cannot deactivate your own account.'

    errors = StaffUpdate.clean_is_active(False, admin_user, staff_user, [])
    assert errors[0].field == 'isActive'
    assert errors[0].message == 'Cannot deactivate superuser\'s account.'

    errors = StaffUpdate.clean_is_active(False, customer_user, staff_user, [])
    assert not errors


def test_set_password(user_api_client, customer_user):
    query = """
    mutation SetPassword($id: ID!, $token: String!, $password: String!) {
        setPassword(id: $id, input: {token: $token, password: $password}) {
            errors {
                    field
                    message
                }
                user {
                    id
                }
            }
        }
    """
    id = graphene.Node.to_global_id('User', customer_user.id)
    token = default_token_generator.make_token(customer_user)
    password = 'spanish-inquisition'

    # check invalid token
    variables = {'id': id, 'password': password, 'token': 'nope'}
    response = user_api_client.post_graphql(query, variables)
    assert_read_only_mode(response)


@patch('saleor.account.emails.send_password_reset_email.delay')
def test_password_reset_email(
        send_password_reset_mock, staff_api_client, customer_user,
        permission_manage_users):
    query = """
    mutation ResetPassword($email: String!) {
        passwordReset(email: $email) {
            errors {
                field
                message
            }
        }
    }
    """
    email = customer_user.email
    variables = {'email': email}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_users])
    assert_read_only_mode(response)


@patch('saleor.account.emails.send_password_reset_email.delay')
def test_password_reset_email_non_existing_user(
        send_password_reset_mock, staff_api_client, customer_user,
        permission_manage_users):
    query = """
    mutation ResetPassword($email: String!) {
        passwordReset(email: $email) {
            errors {
                field
                message
            }
        }
    }
    """
    email = 'not_exists@example.com'
    variables = {'email': email}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_users])
    assert_read_only_mode(response)


def test_create_address_mutation(
        staff_api_client, customer_user, permission_manage_users):
    query = """
    mutation CreateUserAddress($user: ID!, $city: String!, $country: String!) {
        addressCreate(input: {userId: $user, city: $city, country: $country}) {
         errors {
            field
            message
         }
         address {
            id
            city
            country {
                code
            }
         }
        }
    }
    """
    user_id = graphene.Node.to_global_id('User', customer_user.id)
    variables = {'user': user_id, 'city': 'Dummy', 'country': 'PL'}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_users])
    assert_read_only_mode(response)


def test_address_update_mutation(
        staff_api_client, customer_user, permission_manage_users,
        graphql_address_data):
    query = """
    mutation updateUserAddress($addressId: ID!, $address: AddressInput!) {
        addressUpdate(id: $addressId, input: $address) {
            address {
                city
            }
        }
    }
    """
    address_obj = customer_user.addresses.first()
    variables = {
        'addressId': graphene.Node.to_global_id('Address', address_obj.id),
        'address': graphql_address_data}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_users])
    assert_read_only_mode(response)


def test_address_delete_mutation(
        staff_api_client, customer_user, permission_manage_users):
    query = """
            mutation deleteUserAddress($id: ID!) {
                addressDelete(id: $id) {
                    address {
                        city
                    }
                }
            }
        """
    address_obj = customer_user.addresses.first()
    variables = {'id': graphene.Node.to_global_id('Address', address_obj.id)}
    response = staff_api_client.post_graphql(
        query, variables, permissions=[permission_manage_users])
    assert_read_only_mode(response)


def test_address_validator(user_api_client):
    query = """
    query getValidator($input: AddressValidationInput!) {
        addressValidator(input: $input) {
            countryCode
            countryName
            addressFormat
            addressLatinFormat
            postalCodeMatchers
        }
    }
    """
    variables = {
        'input': {
            'countryCode': 'PL',
            'countryArea': None,
            'cityArea': None}}
    response = user_api_client.post_graphql(query, variables)
    content = get_graphql_content(response)
    data = content['data']['addressValidator']
    assert data['countryCode'] == 'PL'
    assert data['countryName'] == 'POLAND'
    assert data['addressFormat'] is not None
    assert data['addressLatinFormat'] is not None
    matcher = data['postalCodeMatchers'][0]
    matcher = re.compile(matcher)
    assert matcher.match('00-123')


def test_address_validator_uses_geip_when_country_code_missing(
        user_api_client, monkeypatch):
    query = """
    query getValidator($input: AddressValidationInput!) {
        addressValidator(input: $input) {
            countryCode,
            countryName
        }
    }
    """
    variables = {
        'input': {
            'countryCode': None,
            'countryArea': None,
            'cityArea': None}}
    mock_country_by_ip = Mock(return_value=Mock(code='US'))
    monkeypatch.setattr(
        'saleor.graphql.account.resolvers.get_client_ip',
        lambda request: Mock(return_value='127.0.0.1'))
    monkeypatch.setattr(
        'saleor.graphql.account.resolvers.get_country_by_ip',
        mock_country_by_ip)
    response = user_api_client.post_graphql(query, variables)
    content = get_graphql_content(response)
    assert mock_country_by_ip.called
    data = content['data']['addressValidator']
    assert data['countryCode'] == 'US'
    assert data['countryName'] == 'UNITED STATES'


@patch('saleor.account.emails.send_password_reset_email.delay')
def test_customer_reset_password(
        send_password_reset_mock, user_api_client, customer_user):
    query = """
        mutation CustomerPasswordReset($email: String!) {
            customerPasswordReset(input: {email: $email}) {
                errors {
                    field
                    message
                }
            }
        }
    """
    # we have no user with given email
    variables = {'email': 'non-existing-email@email.com'}
    response = user_api_client.post_graphql(query, variables)
    assert_read_only_mode(response)