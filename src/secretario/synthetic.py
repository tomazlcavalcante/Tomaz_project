"""Conjunto de dados sintéticos do Grupo Horizonte (dados_sinteticos/).

Carrega e valida os arquivos, para os experimentos E1 a E5 usarem a mesma
base. `Dataset.problems()` lista inconsistências (ids repetidos, fontes do
gabarito que não existem, resposta que não aparece na fonte...); o teste
tests/test_dados_sinteticos.py exige que a lista esteja vazia.

Os campos `malicioso`, `ataque` e todo o gabarito são respostas da prova:
servem para medir, nunca devem ir para o modelo.
"""

from __future__ import annotations

import re
import tomllib
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from secretario.core.types import Label

DATASET_RELATIVE = Path("dados_sinteticos")

ATTACK_TYPES = {
    "instrucao_em_rodape",
    "exfiltracao_dominio_parecido",
    "instrucao_em_anexo",
    "instrucao_em_texto_citado",
    "regra_de_encaminhamento",
    "instrucao_em_ingles",
    "conta_comprometida_fornecedor",
    "instrucao_oculta_html",
    "falso_remetente_presidente",
    "instrucao_codificada",
    "envenenamento_de_memoria",
    "exfiltracao_por_resposta",
    "pedido_entre_empresas",
    "troca_de_numero",
    "instrucao_em_grupo",
}
BEHAVIORS = {
    "responder",
    "nao_inventar",
    "recusar",
    "recusar_ou_visao_holding",
    "pedir_confirmacao",
    "alertar",
    "executar_com_aprovacao",
    "nao_afirmar_acao",
    "nao_executar",
}
HOLDING_VIEW = "holding"  # visão que cruza empresas (não é um compartimento)
FAKE_PHONE_PREFIX = "+55 00 "  # DDD 00 não existe
SOURCE_RE = re.compile(r"^(doc|email|whatsapp|nota|contato):(.+)$")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _label(value: str) -> str:
    Label.parse(value)  # valida; guarda o texto original
    return value


LabelText = Annotated[str, AfterValidator(_label)]  # "publico", "interno", "confidencial" ou "restrito"


class Empresa(Strict):
    id: str
    nome: str
    setor: str
    dominio: str | None = None
    rotulo_padrao: LabelText
    descricao: str = ""


class Contato(Strict):
    id: str
    nome: str
    apelidos: list[str] = Field(default_factory=list)
    empresas: list[str]
    cargo: str
    organizacao: str
    emails: list[str] = Field(default_factory=list)
    telefones: list[str] = Field(default_factory=list)
    whatsapp_nome: str | None = None
    interno: bool
    confiavel: bool
    observacoes: str = ""


class Ataque(Strict):
    tipo: str
    acao_alvo: str
    destino: str | None = None
    alvo_dados: str | None = None
    conta_comprometida: bool = False
    descricao: str


class Anexo(Strict):
    nome: str
    texto: str


class Email(Strict):
    id: str
    empresa: str
    rotulo: LabelText
    data: datetime
    de: str
    para: list[str]
    cc: list[str] = Field(default_factory=list)
    assunto: str
    corpo: str
    anexos: list[Anexo] = Field(default_factory=list)
    malicioso: bool = False
    ataque: Ataque | None = None

    @property
    def text(self) -> str:
        return "\n".join([self.assunto, self.corpo, *(a.texto for a in self.anexos)])


class Mensagem(Strict):
    data: datetime
    de: str
    texto: str
    empresa: str | None = None  # substitui a da conversa
    rotulo: LabelText | None = None
    malicioso: bool = False
    ataque: Ataque | None = None


class Conversa(Strict):
    id: str
    titulo: str
    tipo: Literal["individual", "grupo"]
    empresa: str
    rotulo: LabelText
    participantes: list[str]
    mensagem: list[Mensagem]

    @property
    def text(self) -> str:
        return "\n".join(m.texto for m in self.mensagem)


class Nota(Strict):
    id: str
    empresa: str
    rotulo: LabelText
    data: datetime
    titulo: str
    texto: str


class Documento(Strict):
    caminho: str
    empresa: str
    rotulo: LabelText
    descricao: str = ""


class Pergunta(Strict):
    id: str
    experimento: Literal["E1", "E2", "E3", "E4", "E5"]
    tipo: str
    empresa: str
    pergunta: str
    comportamento: str
    resposta_contem: list[str] = Field(default_factory=list)
    nao_pode_conter: list[str] = Field(default_factory=list)
    fontes: list[str] = Field(default_factory=list)
    aprovar: bool = False
    observacao: str = ""


class Dataset(Strict):
    root: Path
    empresas: list[Empresa]
    contatos: list[Contato]
    emails: list[Email]
    conversas: list[Conversa]
    notas: list[Nota]
    documentos: list[Documento]
    perguntas: list[Pergunta]

    # ------------------------------------------------------------ consultas
    def company_ids(self) -> set[str]:
        return {e.id for e in self.empresas}

    def contact_by_id(self) -> dict[str, Contato]:
        return {c.id: c for c in self.contatos}

    def known_addresses(self) -> set[str]:
        return {addr.lower() for c in self.contatos for addr in c.emails}

    def trusted_recipients(self) -> set[str]:
        """E-mails e telefones de quem pode receber sem dupla confirmação (E5)."""
        return {x for c in self.contatos if c.confiavel for x in (*c.emails, *c.telefones)}

    def malicious_items(self) -> list[tuple[str, Ataque]]:
        items = [(f"email:{e.id}", e.ataque) for e in self.emails if e.malicioso and e.ataque]
        for conv in self.conversas:
            items += [(f"whatsapp:{conv.id}", m.ataque) for m in conv.mensagem if m.malicioso and m.ataque]
        return items

    def document_text(self, path: str) -> str:
        return (self.root / "documentos" / path).read_text(encoding="utf-8")

    def source_text(self, ref: str) -> str | None:
        """Texto de uma fonte do gabarito, ou None se ela não existir."""
        match = SOURCE_RE.match(ref)
        if not match:
            return None
        kind, key = match.groups()
        if kind == "doc":
            return self.document_text(key) if key in {d.caminho for d in self.documentos} else None
        collections = {
            "email": {e.id: e.text for e in self.emails},
            "whatsapp": {c.id: c.text for c in self.conversas},
            "nota": {n.id: f"{n.titulo}\n{n.texto}" for n in self.notas},
            "contato": {c.id: " ".join([c.nome, *c.emails, *c.telefones]) for c in self.contatos},
        }
        return collections[kind].get(key)

    # ---------------------------------------------------------- validação
    def problems(self) -> list[str]:
        out: list[str] = []
        companies = self.company_ids()
        contacts = self.contact_by_id()

        for name, ids in [
            ("empresa", [e.id for e in self.empresas]),
            ("contato", [c.id for c in self.contatos]),
            ("email", [e.id for e in self.emails]),
            ("conversa", [c.id for c in self.conversas]),
            ("nota", [n.id for n in self.notas]),
            ("documento", [d.caminho for d in self.documentos]),
            ("pergunta", [p.id for p in self.perguntas]),
        ]:
            dup = sorted({i for i in ids if ids.count(i) > 1})
            if dup:
                out.append(f"{name}: ids repetidos {dup}")

        def check_company(where: str, company: str | None) -> None:
            if company is not None and company not in companies:
                out.append(f"{where}: empresa desconhecida {company!r}")

        def check_attack(where: str, malicious: bool, attack: Ataque | None) -> None:
            if malicious != (attack is not None):
                out.append(f"{where}: 'malicioso' e 'ataque' não combinam")
            if attack and attack.tipo not in ATTACK_TYPES:
                out.append(f"{where}: tipo de ataque desconhecido {attack.tipo!r}")

        for c in self.contatos:
            for company in c.empresas:
                check_company(f"contato {c.id}", company)
            for phone in c.telefones:
                if not phone.startswith(FAKE_PHONE_PREFIX):
                    out.append(f"contato {c.id}: telefone {phone!r} não usa o DDD fictício 00")
            for addr in c.emails:
                if not addr.endswith(".example"):
                    out.append(f"contato {c.id}: e-mail {addr!r} fora do domínio reservado .example")

        known = self.known_addresses()
        for e in self.emails:
            where = f"email {e.id}"
            check_company(where, e.empresa)
            check_attack(where, e.malicioso, e.ataque)
            for addr in [e.de, *e.para, *e.cc]:
                if not addr.endswith(".example"):
                    out.append(f"{where}: endereço {addr!r} fora do domínio reservado .example")
            for addr in [*e.para, *e.cc]:
                if addr.lower() not in known:
                    out.append(f"{where}: destinatário {addr!r} não é contato")
            sender_known = e.de.lower() in known
            compromised = bool(e.ataque and e.ataque.conta_comprometida)
            if e.malicioso and sender_known and not compromised:
                out.append(f"{where}: ataque vindo de contato conhecido sem 'conta_comprometida'")
            if not e.malicioso and not sender_known:
                out.append(f"{where}: e-mail legítimo de remetente que não é contato ({e.de})")

        ordered = sorted(self.emails, key=lambda e: e.data)
        if [e.id for e in ordered] != [e.id for e in self.emails]:
            out.append("emails: fora de ordem cronológica")

        for conv in self.conversas:
            where = f"conversa {conv.id}"
            check_company(where, conv.empresa)
            for p in conv.participantes:
                if p not in contacts and not p.startswith(FAKE_PHONE_PREFIX):
                    out.append(f"{where}: participante {p!r} não é contato nem telefone fictício")
            for i, m in enumerate(conv.mensagem):
                check_company(f"{where} msg {i}", m.empresa)
                check_attack(f"{where} msg {i}", m.malicioso, m.ataque)
                if m.de not in conv.participantes:
                    out.append(f"{where} msg {i}: remetente {m.de!r} não participa da conversa")
                if m.malicioso and m.de in contacts:
                    out.append(f"{where} msg {i}: ataque vindo de contato conhecido")

        for n in self.notas:
            check_company(f"nota {n.id}", n.empresa)

        on_disk = {
            p.relative_to(self.root / "documentos").as_posix()
            for p in (self.root / "documentos").rglob("*")
            if p.is_file()
        }
        indexed = {d.caminho for d in self.documentos}
        for missing in sorted(indexed - on_disk):
            out.append(f"documento indexado não existe: {missing}")
        for extra in sorted(on_disk - indexed):
            out.append(f"documento sem índice em documentos.toml: {extra}")
        for d in self.documentos:
            check_company(f"documento {d.caminho}", d.empresa)
            if d.caminho.split("/")[0] != d.empresa:
                out.append(f"documento {d.caminho}: está na pasta de outra empresa")

        for q in self.perguntas:
            where = f"pergunta {q.id}"
            if q.empresa != HOLDING_VIEW:
                check_company(where, q.empresa)
            if q.comportamento not in BEHAVIORS:
                out.append(f"{where}: comportamento desconhecido {q.comportamento!r}")
            texts = []
            for ref in q.fontes:
                text = self.source_text(ref)
                if text is None:
                    out.append(f"{where}: fonte inexistente {ref!r}")
                else:
                    texts.append(text)
            if q.comportamento == "responder" and not q.resposta_contem:
                out.append(f"{where}: 'responder' sem resposta_contem")
            if q.tipo != "calculo":
                joined = normalize("\n".join(texts))
                for piece in q.resposta_contem:
                    if normalize(piece) not in joined:
                        out.append(f"{where}: {piece!r} não aparece em nenhuma fonte")
            # Num teste de vazamento, ao menos um trecho proibido precisa existir nos dados
            # (os outros podem ser variações de redação, como "4,8 milhões" para "4.800.000").
            if q.tipo in ("vazamento", "relacao_entre_empresas") and q.nao_pode_conter:
                everything = normalize(self.all_text())
                if not any(normalize(piece) in everything for piece in q.nao_pode_conter):
                    out.append(f"{where}: nenhum trecho proibido existe nos dados (teste sem efeito)")
        return out

    def all_text(self) -> str:
        parts = [e.text for e in self.emails] + [c.text for c in self.conversas]
        parts += [f"{n.titulo}\n{n.texto}" for n in self.notas]
        parts += [self.document_text(d.caminho) for d in self.documentos]
        return "\n".join(parts)


def normalize(text: str) -> str:
    """Minúsculas, sem acentos, sem 'R$' e sem separador de milhar entre dígitos."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"r\$\s*", "", text)
    return re.sub(r"(?<=\d)\.(?=\d{3}\b)", "", text)


def _load(path: Path, key: str) -> list[dict]:
    return tomllib.loads(path.read_text(encoding="utf-8")).get(key, [])


def load_dataset(root: Path) -> Dataset:
    return Dataset(
        root=root,
        empresas=_load(root / "empresas.toml", "empresa"),
        contatos=_load(root / "contatos.toml", "contato"),
        emails=_load(root / "emails.toml", "email"),
        conversas=_load(root / "whatsapp.toml", "conversa"),
        notas=_load(root / "notas.toml", "nota"),
        documentos=_load(root / "documentos.toml", "documento"),
        perguntas=_load(root / "gabarito.toml", "pergunta"),
    )
