# -*- coding: utf-8 -*-
from django.contrib import messages
from django import http
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect
from django.utils.translation import ugettext_lazy as _
from oscar.core.loading import get_class
from ecommerce.website.utils import remove_message
#from libs.cobrebem.facade import Facade
from libs.moedadigital import facade as moedadigital
from libs.cobrebem import facade as cobrebem

from oscar.apps.payment.exceptions import UnableToTakePayment
from oscar.apps.checkout import views
from libs.crm.mixins import OscarToCRMMixin
import logging

BankcardForm = get_class('payment.forms', 'BankcardForm')
SourceType = get_class('payment.models', 'SourceType')
Source = get_class('payment.models', 'Source')

log = logging.getLogger('ecommerce.checkout.views')


class PaymentDetailsView(views.PaymentDetailsView, OscarToCRMMixin):
    """
    For taking the details of payment and creating the order

    The class is deliberately split into fine-grained methods, responsible for
    only one thing.  This is to make it easier to subclass and override just
    one component of functionality.

    All projects will need to subclass and customise this class.
    """

    preview = False

    def get(self, request, *args, **kwargs):
        error_response = self.get_error_response()
        if error_response:
            return error_response
        return super(PaymentDetailsView, self).get(request, *args, **kwargs)

    def get_error_response(self):
        if self.request.method == 'POST':
            if not self.valida_contrato_ssl() or not self.valida_contrato_sitemonitorado() or \
                    not self.valida_contrato_siteseguro() or not self.valida_contrato_pki():
                messages.error(self.request, 'Você precisa aceitar os termos de uso dos produtos selecionados')
                return HttpResponseRedirect(reverse('checkout:payment-details'))
        return super(PaymentDetailsView, self).get_error_response()

    def valida_contrato_ssl(self):
        return not self.request.basket.tem_contrato_ssl() or self.request.POST.get('aceita_contrato_ssl', '') == '1'

    def valida_contrato_siteseguro(self):
        return not self.request.basket.tem_contrato_siteseguro() or self.request.POST.get('aceita_contrato_ss', '') == '1'

    def valida_contrato_sitemonitorado(self):
        return not self.request.basket.tem_contrato_sitemonitorado() or self.request.POST.get('aceita_contrato_sm', '') == '1'

    def valida_contrato_pki(self):
        return not self.request.basket.tem_contrato_pki() or self.request.POST.get('aceita_contrato_pki', '') == '1'

    def get_context_data(self, **kwargs):
        # Add here anything useful to be rendered in templates
        ctx = super(PaymentDetailsView, self).get_context_data(**kwargs)
        ctx['bankcard_form'] = kwargs.get('bankcard_form', BankcardForm())

        # Payment DIV from MoedaDigital:
        facade = moedadigital.Facade()
        payment_div = facade.get_payment_html(request=self.request, amount=ctx['order_total'].incl_tax)
        ctx['payment_div'] = payment_div

        return ctx

    def post(self, request, *args, **kwargs):
        """
        This method is designed to be overridden by subclasses which will
        validate the forms from the payment details page.  If the forms are
        valid then the method can call submit()
        """
        error_response = self.get_error_response()
        if error_response:
            return error_response

        # Only for moeda-digital
        if request.POST.get('source-type') == 'akatus':
            payment_method = request.POST.get('MD_MeioPagto')
            amount = request.POST.get('MD_ValorBoleto')
            installments = request.POST.get('MD_FormaPagto')
            installment = request.POST.get('MD_ValorParcela')
            parameters = {
                    'payment_method': payment_method,
                    'amount': amount,
                    'installments': installments,
                    'installment': installment,
                    }
            submission = self.build_submission(payment_kwargs=parameters)
            return self.submit(**submission)

        # Only for credit card:
        if request.POST.get('source-type') == 'credito':
            bankcard_form = BankcardForm(request.POST)
            if not bankcard_form.is_valid():
                # Bancard form invalid, re-render the payment details template
                self.preview = False
                ctx = self.get_context_data(**kwargs)
                ctx['bankcard_form'] = bankcard_form
                return self.render_to_response(ctx)
            payment_kwargs = self.build_payment_kwargs(request)
            submission = self.build_submission(payment_kwargs=payment_kwargs)
            return self.submit(**submission)

        if request.POST.get('source-type') == 'debito':
            bank = request.POST.get('card_type')
            submission = self.build_submission()
            return self.submit(**submission)

        if request.POST.get('source-type') == 'boleto':
            submission = self.build_submission()
            return self.submit(**submission)

    def build_submission(self, **kwargs):
        """
        Author: Alessandro Reichert
        Return a dict of data to submitted to pay for, and create an order
        ** Update the payment_kwargs which is not treated in the standard oscar
        """
        submission = super(PaymentDetailsView, self).build_submission(**kwargs)
        if 'payment_kwargs' in kwargs:
            payment_kwargs = kwargs.get('payment_kwargs')
            submission.update({'payment_kwargs': payment_kwargs})
        return submission

    def build_payment_kwargs(self, request, *args, **kwargs):
        """
        Author: Alessandro Reichert
        This method is responsible for generating the payment information (bankcard),
         which will be sent to handle_payment and can be used for the submission to CobreBem
        """
        # Double-check the bankcard data is still valid
        bankcard_form = BankcardForm(request.POST)
        if not bankcard_form.is_valid():
            messages.error(request, _("Invalid submission"))
            return http.HttpResponseRedirect(
                reverse('checkout:payment-details'))

        bankcard = bankcard_form.bankcard
        payment_kwargs={'bankcard': bankcard}
        return payment_kwargs

    def handle_payment(self, order_number, total_incl_tax, **kwargs):
        """
        This method is responsible for taking the payment.
        """
        payment_source = self.request.POST.get('source-type')

        if payment_source == 'akatus-boleto':
            return self.handle_payment_boleto(order_number, total_incl_tax, **kwargs)

        elif payment_source == 'akatus-creditcard':
            return self.handle_payment_credito(order_number, total_incl_tax, **kwargs)

        elif payment_source == 'akatus-debitcard':
            return self.handle_payment_debito(order_number, total_incl_tax, **kwargs)

        raise UnableToTakePayment

    def handle_payment_credito(self, order_number, total_incl_tax, **kwargs):
        """
        This method is responsible for taking the payment of credit card
        """
        bankcard = kwargs['bankcard']

        # Make request to DataCash - if there any problems (eg bankcard
        # not valid / request refused by bank) then an exception would be
        # raised and handled by the parent PaymentDetail view)
        facade = cobrebem.Facade()
        transaction_id = facade.approval(self.request, order_number, total_incl_tax.incl_tax, bankcard)
        if transaction_id:
            msg = facade.capture(transaction_id)
            if transaction_id != msg:
                raise UnableToTakePayment(msg)

        bankcard.user = self.request.user
        bankcard.save()

        # Request was successful - record the "payment source".  As this
        # request was a 'pre-auth', we set the 'amount_allocated' - if we had
        # performed an 'auth' request, then we would set 'amount_debited'.
        source_type, _ = SourceType.objects.get_or_create(name='cobrebem-credito')
        source = Source(source_type=source_type,
                        currency='BRL',
                        amount_debited=total_incl_tax.incl_tax,
                        reference=transaction_id,
                        bankcard=bankcard)
        # When we create a transaction, we have to set a txn_type that should be debit or refund
        source.create_deferred_transaction("debit", total_incl_tax.incl_tax, reference=transaction_id, status=1)
        self.add_payment_source(source)

        # Also record payment event
        # When we create the payment event, we have to set a txn_type that should be debit or refund
        self.add_payment_event('debit', total_incl_tax.incl_tax, reference=transaction_id)

    def handle_payment_debito(self, order_number, total_incl_tax, **kwargs):
        """
        This method is responsible for taking the payment of credit card
        """
        ip = self.request.META['REMOTE_ADDR']
        bankcard = kwargs['bankcard']

        # Make request to DataCash - if there any problems (eg bankcard
        # not valid / request refused by bank) then an exception would be
        # raised and handled by the parent PaymentDetail view)
        facade = cobrebem.Facade()
        debito_html = facade.debit_card(self.request, order_number, total_incl_tax.incl_tax, bankcard)

        if not debito_html:
            msg = 'Falha no pagamento com cartão de débito'
            raise UnableToTakePayment(msg)

        bankcard.user = self.request.user
        bankcard.save()

        # Request was successful - record the "payment source".  As this
        # request was a 'pre-auth', we set the 'amount_allocated' - if we had
        # performed an 'auth' request, then we would set 'amount_debited'.
        source_type, _ = SourceType.objects.get_or_create(name='cobrebem-debito')
        source = Source(source_type=source_type,
                        currency='BRL',
                        amount_allocated=total_incl_tax.incl_tax,
                        bankcard=bankcard)
        source.create_deferred_transaction("auth_request", total_incl_tax.incl_tax, status=1)
        self.add_payment_source(source)

        # Also record payment event
        self.add_payment_event('auth_request', total_incl_tax.incl_tax)

    def handle_payment_boleto(self, order_number, total_incl_tax, **kwargs):
        """
        This method is responsible for taking the payment of credit card
        """
        ip = self.request.META['REMOTE_ADDR']
        bankcard = kwargs['bankcard']

        # Do the request to Cobrebem
        facade = cobrebem.Facade()
        boleto_html = facade.get_boleto(self.request, order_number, total_incl_tax.incl_tax)
        if boleto_html.status_code != 200:
            msg = 'Falha no pagamento com boleto bancário'
            raise UnableToTakePayment(msg)

        # TODO: recuperar o nosso número do boleto:
        nosso_numero = 'nosso numero'

        bankcard.user = self.request.user
        bankcard.boleto_html = boleto_html
        bankcard.save()

        # Request was successful - record the "payment source".  As this
        # request was a 'pre-auth', we set the 'amount_allocated' - if we had
        # performed an 'auth' request, then we would set 'amount_debited'.
        source_type, _ = SourceType.objects.get_or_create(name='cobrebem-boleto')
        source = Source(source_type=source_type,
                        currency='BRL',
                        amount_allocated=total_incl_tax.incl_tax,
                        reference=nosso_numero)
        source.create_deferred_transaction("boleto_emitido", total_incl_tax.incl_tax, reference=nosso_numero, status=1)
        self.add_payment_source(source)

        # Also record payment event
        self.add_payment_event('boleto_emitido', total_incl_tax.incl_tax)

    def handle_order_placement(self, order_number, user, basket, shipping_address, shipping_method, total, **kwargs):
        """
        Write out the order models and return the appropriate HTTP response

        We deliberately pass the basket in here as the one tied to the request
        isn't necessarily the correct one to use in placing the order.  This
        can happen when a basket gets frozen.
        """
        order = self.place_order(order_number, user, basket, shipping_address, shipping_method, total, **kwargs)
        basket.submit()

        self.set_status_pago(order)

        return self.handle_successful_order(order)


    def set_status_pago(self, order):
        sources = order.sources.all()
        if sources and sources[0].reference:  # somente deve setar se existir um transaction_id(reference)
            order.set_status('Pago')
            log.info('Order #%s: alterado status para Pago' % order.pk)


class ShippingAddressView(views.ShippingAddressView):

    def get(self, request, *args, **kwargs):

        r = super(ShippingAddressView, self).get(request, *args, **kwargs)
        remove_message(request, u'Sua cesta não requer um endereço para ser submetida')
        return r