"""O conjunto de dados sintéticos é consistente e cobre o que os experimentos precisam."""

from __future__ import annotations

from collections import Counter

import pytest
from pydantic import ValidationError

from secretario.synthetic import ATTACK_TYPES, DATASET_RELATIVE, Email, load_dataset, normalize

from .conftest import PROJECT_ROOT


@pytest.fixture(scope="module")
def ds():
    return load_dataset(PROJECT_ROOT / DATASET_RELATIVE)


def test_no_consistency_problems(ds):
    assert ds.problems() == []


def test_every_company_has_material(ds):
    for company in ds.company_ids() - {"pessoal"}:
        assert any(d.empresa == company for d in ds.documentos), company
        assert any(e.empresa == company for e in ds.emails), company
    assert any(e.empresa == "pessoal" for e in ds.emails)
    assert any(c.empresa == "pessoal" for c in ds.conversas)


def test_attacks_cover_every_type_and_channel(ds):
    attacks = ds.malicious_items()
    assert {a.tipo for _, a in attacks} == ATTACK_TYPES  # cada tipo aparece ao menos uma vez
    channels = Counter(ref.split(":")[0] for ref, _ in attacks)
    assert channels["email"] >= 10 and channels["whatsapp"] >= 2
    assert any(a.conta_comprometida for _, a in attacks)
    assert any(e.malicioso and e.anexos for e in ds.emails)  # injeção dentro de anexo


def test_attackers_are_not_trusted_recipients(ds):
    trusted = ds.trusted_recipients()
    for _, attack in ds.malicious_items():
        if attack.destino and not attack.conta_comprometida:
            assert attack.destino not in trusted, attack.tipo


def test_malicious_and_legit_are_mixed_in_time(ds):
    """Os ataques não ficam todos no fim: o modelo os encontra no meio do trabalho normal."""
    flags = [e.malicioso for e in ds.emails]
    assert 10 <= sum(flags) <= len(flags) // 2
    assert not flags[0] and not all(flags[-5:])


def test_hard_cases_exist(ds):
    contacts = ds.contact_by_id()
    # homônimos
    assert contacts["C12"].nome in contacts["C13"].nome
    # contatos externos compartilhados entre empresas
    assert any(not c.interno and len(c.empresas) >= 2 for c in ds.contatos)
    # conversa de WhatsApp que mistura empresas
    assert any(any(m.empresa and m.empresa != c.empresa for m in c.mensagem) for c in ds.conversas)
    # fato que muda: e-mail diz 15h, WhatsApp mais recente diz 16h
    assert "15h" in ds.source_text("email:E14") and "16h" in ds.source_text("whatsapp:W01")


def test_answer_key_covers_each_experiment(ds):
    by_exp = Counter(q.experimento for q in ds.perguntas)
    assert by_exp["E2"] >= 3 and by_exp["E3"] >= 10 and by_exp["E4"] >= 8 and by_exp["E5"] >= 6
    behaviors = {q.comportamento for q in ds.perguntas}
    assert {"responder", "nao_inventar", "recusar", "nao_afirmar_acao", "executar_com_aprovacao"} <= behaviors


def test_normalize_ignores_accents_currency_and_thousands():
    assert normalize("R$ 4.800.000,00") == normalize("4800000,00")
    assert normalize("Maré Alimentos") == "mare alimentos"
    assert normalize("versão 1.5") == "versao 1.5"  # decimal com um dígito não é milhar


def test_problems_catches_broken_data(ds):
    broken = ds.model_copy(deep=True)
    broken.emails[0].empresa = "inexistente"
    broken.emails[1].malicioso = True  # sem a descrição do ataque
    broken.perguntas[0].fontes = ["doc:nao/existe.md"]
    broken.perguntas[1].resposta_contem = ["valor que não está na fonte"]
    problems = "\n".join(broken.problems())
    assert "empresa desconhecida 'inexistente'" in problems
    assert "'malicioso' e 'ataque' não combinam" in problems
    assert "fonte inexistente 'doc:nao/existe.md'" in problems
    assert "não aparece em nenhuma fonte" in problems


def test_invalid_label_is_rejected(ds):
    data = ds.emails[0].model_dump()
    data["rotulo"] = "ultrassecreto"
    with pytest.raises(ValidationError, match="sigilo"):
        Email.model_validate(data)
