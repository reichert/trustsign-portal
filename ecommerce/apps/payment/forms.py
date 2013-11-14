# -*- coding: utf-8 -*-
from django.forms import CharField, ModelForm
from localflavor.br.forms import BRCPFField
from oscar.apps.payment.forms import BankcardNumberField, BankcardCCVField, BankcardExpiryMonthField
from oscar.core.loading import get_class
from ecommerce.apps.payment.models import Debitcard

Bankcard = get_class('payment.models', 'Bankcard')


class BankcardForm(ModelForm):
    name = CharField(label='Titular')
    number = BankcardNumberField()
    ccv = BankcardCCVField({'label': 'Código de Segurança'})
    expiry_month = BankcardExpiryMonthField()
    credito_cpf = BRCPFField(label='CPF do portador')
    credito_telefone = CharField(label='Telefone do portador')

    class Meta:
        model = Bankcard
        fields = ('name', 'number', 'expiry_month', 'ccv', 'credito_cpf', 'credito_telefone')

    def save(self, *args, **kwargs):
        # It doesn't really make sense to save directly from the form as saving
        # will obfuscate some of the card details which you normally need to
        # pass to a payment gateway.  Better to use the bankcard property below
        # to get the cleaned up data, then once you've used the sensitive
        # details, you can save.
        raise RuntimeError("Don't save bankcards directly from form")

    def clean(self):
        cleaned_data = super(BankcardForm, self).clean()
        return cleaned_data

    @property
    def bankcard(self):
        """
        Return an instance of the Bankcard model (unsaved)
        """
        return Bankcard(number=self.cleaned_data['number'],
                        expiry_date=self.cleaned_data['expiry_month'],
                        name=self.cleaned_data['name'],
                        ccv=self.cleaned_data['ccv'],
                        credito_cpf=self.cleaned_data['credito_cpf'],
                        credito_telefone=self.cleaned_data['credito_telefone'])


class DebitcardForm(ModelForm):

    class Meta:
        model = Debitcard
        fields = ['banco']

    @property
    def debitcard(self):
        return Debitcard(banco=self.cleaned_data['banco'])