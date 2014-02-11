# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import logging

from django.conf import settings
from django.core.cache import cache
import requests

from ecommerce.certificados.models import Voucher
from ecommerce.website.utils import send_template_email
from portal.suporte.utils import url_parse, get_emails_dominio


log = logging.getLogger('libs.comodo')

# CÓDIGOS ANTIGOS
#CODIGO_SSL = 488
#CODIGO_SSL_WILDCARD = 489
#CODIGO_SSL_SAN = 492
#CODIGO_SSL_EV = 337
#CODIGO_SSL_EV_MDC = 410
#CODIGO_MDC = 335
#CODIGO_TRIAL = 342

# CÓDIGOS NOVOS
CODIGO_SSL = 301
CODIGO_SSL_WILDCARD = 343
CODIGO_SSL_SAN = 492
CODIGO_SSL_EV = 337
CODIGO_SSL_EV_MDC = 410
CODIGO_MDC = 335
CODIGO_TRIAL = 43  # 342 é o antigo (trial de 90 dias)

CODIGOS_PRODUTOS = {
    Voucher.PRODUTO_SSL: CODIGO_SSL,
    Voucher.PRODUTO_SSL_WILDCARD: CODIGO_SSL_WILDCARD,
    Voucher.PRODUTO_SAN_UCC: CODIGO_SSL_SAN,
    Voucher.PRODUTO_EV: CODIGO_SSL_EV,
    Voucher.PRODUTO_EV_MDC: CODIGO_SSL_EV_MDC,
    Voucher.PRODUTO_MDC: CODIGO_MDC
}


def log_safe_dict(original_dict, keys):
    d = original_dict.copy()
    for key in keys:
        if key in d:
            d[key] = '******'
    return d

EXCLUDE_KEYS = ['loginName', 'loginPassword']


class ComodoError(Exception):
    code = None
    comodo_message = None

    def __init__(self, *args, **kwargs):
        self.code = kwargs.pop('code', -1)
        self.comodo_message = kwargs.pop('comodo_message', 'Error')
        super(ComodoError, self).__init__(*args, **kwargs)


class EmissaoComodoError(ComodoError):
    pass


class ReemissaoComodoError(ComodoError):
    pass


class RevogacaoComodoError(ComodoError):
    pass


class EmissaoJRECSComodoError(ComodoError):
    pass


def limpa_dominio(dominio):

    if dominio.startswith('www.'):
        dominio = dominio[4:]
    if dominio.startswith('*.'):
        dominio = dominio[2:]

    parts = dominio.split('.')

    return '.'.join(parts[-3:] if len(parts[-1]) == 2 else parts[-2:])


def get_emails_validacao_padrao(dominio):
    dominio = limpa_dominio(dominio)

    return [e % dominio for e in ('admin@%s', 'administrator@%s', 'hostmaster@%s', 'postmaster@%s', 'webmaster@%s')]


def get_emails_validacao_whois(dominio):
    emails = cache.get('emails_validacao-{}'.format(dominio))

    if emails is not None:
        return emails

    dominio = limpa_dominio(dominio)

    # if dominio.endswith('.br'):
    #     emails = get_emails_dominio(dominio)
    # else:
    #     emails = []

    response = requests.post(settings.COMODO_API_GET_DCV_EMAILS_URL, data={
        'loginName': settings.COMODO_LOGIN_NAME,
        'loginPassword': settings.COMODO_LOGIN_PASSWORD,
        'domainName': dominio
    })

    emails = [r[12:] for r in response.text.splitlines()
              if r.startswith('whois email\t') and r[12:] not in ('cert@cert.br', 'mail-abuse@cert.br')]

    #emails.extend(email for email in emails_comodo if email not in emails)

    cache.set('emails_validacao-{}'.format(dominio), emails, 86400)  # cache de 1 dia

    return emails


def get_emails_validacao(dominio):
    return get_emails_validacao_padrao(dominio) + get_emails_validacao_whois(dominio)


def envia_email_erro(tipo, voucher, erro_codigo, erro_mensagem):

    if tipo not in ('emissao', 'revogação', 'reemissão'):
        return

    subject = '[ERRO]Ocorreu um erro ao enviar a {} para a Comodo({}) #{}'.format(tipo, erro_codigo, voucher.crm_hash)
    template = 'customer/emails/erro_comodo.html'
    context = {
        'voucher': voucher,
        'erro_comodo': {
            'codigo': erro_codigo,
            'mensagem': erro_mensagem,
        },
    }

    send_template_email([settings.TRUSTSIGN_SISTEMA_EMAIL], subject, template, context)


def emite_certificado(emissao):
    try:
        voucher = emissao.voucher

        if voucher.ssl_term == voucher.VALIDADE_ANUAL:
            validade = 365
        elif voucher.ssl_term == voucher.VALIDADE_BIANUAL:
            validade = 730
        elif voucher.ssl_term == voucher.VALIDADE_TRIANUAL:
            validade = 1095
        elif voucher.ssl_term == voucher.VALIDADE_DEGUSTACAO:
            validade = 30
        else:
            raise ComodoError('Validade inválida para emissão de certificados', code=-1)

        if voucher.ssl_product == 'ssl' and voucher.ssl_term == voucher.VALIDADE_DEGUSTACAO:
            product = CODIGO_TRIAL
        else:
            product = CODIGOS_PRODUTOS[voucher.ssl_product]

        if voucher.ssl_product in (Voucher.PRODUTO_SSL, Voucher.PRODUTO_SSL_WILDCARD, Voucher.PRODUTO_SAN_UCC, Voucher.PRODUTO_MDC):
            ca_certificate_id = 389
        elif voucher.ssl_product in (Voucher.PRODUTO_EV, Voucher.PRODUTO_EV_MDC):
            ca_certificate_id = 391
        else:
            ca_certificate_id = None

        params = {
            'loginName': settings.COMODO_LOGIN_NAME,
            'loginPassword': settings.COMODO_LOGIN_PASSWORD,
            'product': product,
            'days': validade,
            'serverSoftware': emissao.emission_server_type,
            'csr': emissao.emission_csr,
            'prioritiseCSRValues': 'N',
            'streetAddress1': voucher.customer_address1,
            'localityName': voucher.customer_city,
            'stateOrProvinceName': voucher.customer_state,
            'postalCode': voucher.customer_zip,
            'countryName': 'BR',
            'emailAddress': 'certificatebox@trustsign.com.br',
            'isCustomerValidated': 'Y',
            'foreignOrderNumber': emissao.crm_hash,
            #'checkFONIsUnique': 'Y', # comentei só pra ficar mais facil de testar, senao teria q criar um voucher toda hora
            'responseFormat': '1',
            'test': 'Y' if settings.COMODO_ENVIAR_COMO_TESTE else 'N',
            'isAppRepValidated': 'Y',
            'isCallbackCompleted': 'Y'

        }

        if ca_certificate_id:
            params['caCertificateID'] = ca_certificate_id

        if voucher.ssl_product in (voucher.PRODUTO_MDC, voucher.PRODUTO_SAN_UCC, voucher.PRODUTO_EV_MDC):
            params['domainNames'] = emissao.emission_urls
            params['dcvEmailAddresses'] = emissao.emission_dcv_emails
        else:
            params['dcvEmailAddress'] = emissao.emission_dcv_emails

        if voucher.ssl_product in (voucher.PRODUTO_EV, voucher.PRODUTO_EV_MDC):
            params['joiLocalityName'] = voucher.customer_city
            params['joiStateOrProvinceName'] = voucher.customer_state
            params['joiCountryName'] = 'BR'

        response = requests.post(settings.COMODO_API_EMISSAO_URL, params)

        r = url_parse(response.text)

        if r['errorCode'] != '0':
            log.error('ERRO EMISSAO > params: %s | response: %s' % (log_safe_dict(params, EXCLUDE_KEYS), r))
            envia_email_erro('emissão', voucher, r['errorCode'], r['errorMessage'])
            raise EmissaoComodoError('Ocorreu um erro na chamada da COMODO', code=r['errorCode'], comodo_message=r['errorMessage'])
        else:
            log.info('EMISSAO > params: %s \nResponse: %s' % (log_safe_dict(params, EXCLUDE_KEYS), r))

        return r

    except Exception as e:
        if isinstance(e, ComodoError):
            raise e
        log.error('ERRO EMISSAO > erro desconhecido: %s' % e)
        raise EmissaoComodoError('Ocorreu um erro na chamada da COMODO', code='-500', comodo_message='Erro interno do servidor')


def revoga_certificado(revogacao):

    try:
        params = {
            'loginName': settings.COMODO_LOGIN_NAME,
            'loginPassword': settings.COMODO_LOGIN_PASSWORD,
            'orderNumber': revogacao.emission.comodo_order,
            'revocationReason': revogacao.revoke_reason,
            'test': 'Y' if settings.COMODO_ENVIAR_COMO_TESTE else 'N',
            'responseFormat': '1',
        }

        response = requests.post(settings.COMODO_API_REVOGACAO_URL, params)

        r = url_parse(response.text)

        if r['errorCode'] != '0':
            log.error('ERRO REVOGAÇÃO > params: %s | response: %s' % (log_safe_dict(params, EXCLUDE_KEYS), r))
            envia_email_erro('revogação', revogacao.emission.voucher, r['errorCode'], r['errorMessage'])
            raise RevogacaoComodoError('Ocorreu um erro na chamada da COMODO', code=r['errorCode'], comodo_message=r['errorMessage'])
        else:
            log.info('REVOGAÇÃO > params: %s | response: %s' % (log_safe_dict(params, EXCLUDE_KEYS), r))

        return r

    except Exception as e:
        if isinstance(e, ComodoError):
            raise e
        log.error('ERRO REVOGAÇÃO > erro desconhecido: %s' % e)
        raise RevogacaoComodoError('Ocorreu um erro na chamada da COMODO', code='-500', comodo_message='Erro interno do servidor')


def reemite_certificado(emissao):

    try:
        params = {
            'loginName': settings.COMODO_LOGIN_NAME,
            'loginPassword': settings.COMODO_LOGIN_PASSWORD,
            'orderNumber': emissao.comodo_order,
            'csr': emissao.emission_csr,
            'isCustomerValidated': 'Y',
            'foreignOrderNumber': emissao.crm_hash,
            'responseFormat': '1',
            'isAppRepValidated': 'Y',
            'isCallbackCompleted': 'Y'
        }

        response = requests.post(settings.COMODO_API_REEMISSAO_URL, params)

        r = url_parse(response.text)

        if r['errorCode'] != '0':
            log.error('ERRO REEMISSÃO > params: %s | response: %s' % (log_safe_dict(params, EXCLUDE_KEYS), r))
            envia_email_erro('reemissão', emissao.voucher, r['errorCode'], r['errorMessage'])
            raise ReemissaoComodoError('Ocorreu um erro na chamada da COMODO', code=r['errorCode'], comodo_message=r['errorMessage'])
        else:
            log.info('REEMISSÃO > params: %s | response: %s' % (log_safe_dict(params, EXCLUDE_KEYS), r))

        return r
    except Exception as e:
        if isinstance(e, ComodoError):
            raise e
        log.error('ERRO REEMISSÃO > erro desconhecido: %s' % e)
        raise ReemissaoComodoError('Ocorreu um erro na chamada da COMODO', code='-500', comodo_message='Erro interno do servidor')


def emite_jre_cs(emissao):
    try:
        voucher = emissao.voucher
        code_ppp = {
            '1year': 1511,
            '2years': 1512,
            '3years': 1509,
        }
        params = {
            'loginName': settings.COMODO_LOGIN_NAME,
            'loginPassword': settings.COMODO_LOGIN_PASSWORD,

            'ap': '',
            'reseller': 'y',

            '1_PPP': code_ppp[emissao.voucher.ssl_term],
            '1_csr': emissao.emission_csr,

            'organizationName': voucher.customer_companyname,
            'organizationalUnitName': '',
            'postOfficeBox': '',
            'streetAddress1': voucher.customer_address1,
            'streetAddress2': voucher.customer_address2,
            'streetAddress3': voucher.customer_address3,
            'localityName': voucher.customer_city,
            'stateOrProvinceName': voucher.customer_state,
            'postalCode': voucher.customer_zip,
            'countryName': 'BR',
        }

        response = requests.post(settings.COMODO_API_EMISSAO_JRE_CS_URL, params)

        r = url_parse(response.text)

        if r['errorCode'] != '0':
            log.error('ERRO EMISSÃO JRE/CS > params: {} | response: {}'.format(log_safe_dict(params, EXCLUDE_KEYS), r))
            envia_email_erro('emissão', emissao.voucher, r['errorCode'], r['errorMessage'])
            raise EmissaoJRECSComodoError('Ocorreu um erro na chamada da COMODO', code=r['errorCode'], comodo_message=r['errorMessage'])

        log.info('EMISSÂO JRE/CS > params: {} | response: {}'.format(log_safe_dict(params, EXCLUDE_KEYS), r))

        return r
    except Exception as e:
        if isinstance(e, ComodoError):
            raise e
        log.error('ERRO EMISSÃO JRE/CS > erro desconhecido: %s' % e)
        raise EmissaoJRECSComodoError('Ocorreu um erro na chamada da COMODO', code='-500', comodo_message='Erro interno do servidor')