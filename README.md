# MySANA QA Agent

Agente local de QA para automatizar tarefas repetitivas no MySANA: navegar, preencher formulários,
validar workflows, capturar evidências e produzir relatórios PASS/FAIL.

A arquitectura é inspirada no AgenticSeek, mas esta implementação é independente e muito mais pequena,
pensada especificamente para correr num PC normal sem LLM local pesado.

## Objectivo da v0.1

- Browser Chrome visível e perfil persistente para login manual.
- `workflow`: testes determinísticos e baratos, sem IA em cada clique.
- `agent`: exploração limitada com IA quando ainda não existe workflow.
- Router de modelos: **Groq → NVIDIA NIM → OpenAI**.
- OpenAI desactivado por defeito para evitar gasto acidental.
- Whitelist de domínios.
- Bloqueio por defeito de apagar, aprovar, pagar, transferir e rejeitar.
- Screenshot e relatório Markdown/JSON por execução.

## Modelos

Por defeito:

1. `groq/openai/gpt-oss-120b`
2. `nvidia_nim/openai/gpt-oss-120b`
3. `openai/gpt-5.6-luna` — apenas se `QA_ENABLE_PAID_FALLBACK=true`

Se Groq falhar ou atingir quota, o router tenta NVIDIA. A OpenAI nunca é usada automaticamente
enquanto o fallback pago estiver desactivado.

## Instalação no Windows

Requisitos:

- Python 3.11
- Google Chrome
- Uma chave Groq e/ou NVIDIA NIM

PowerShell:

```powershell
.\scripts\setup.ps1
```

Edita `.env` e adiciona as chaves que tiveres.

Depois:

```powershell
.\.venv\Scripts\python.exe -m app.main doctor
```

## Primeiro login

```powershell
.\.venv\Scripts\python.exe -m app.main login
```

O Chrome abre com um perfil próprio em `runtime/chrome-profile`. Faz login manualmente no MySANA.
O projecto não precisa de guardar utilizador/password.

## Inspeccionar um formulário

```powershell
.\.venv\Scripts\python.exe -m app.main inspect
```

Navega até ao formulário pretendido e carrega Enter no terminal. O programa mostra os elementos
interactivos e os respectivos selectores.

## Executar um workflow conhecido

```powershell
.\.venv\Scripts\python.exe -m app.main workflow workflows/example-smoke.json
```

É este o modo que devemos usar para testes repetitivos: é previsível, barato e não depende do LLM
para decidir cada clique.

## Executar modo agentic

```powershell
.\.venv\Scripts\python.exe -m app.main agent "Testa os campos obrigatórios deste formulário sem aprovar nem apagar nada"
```

O agent só pode devolver uma acção de cada vez: `click`, `fill`, `select`, `wait`, `assert_text` ou `done`.
Não pode executar JavaScript, shell ou Python no browser.

## Evidências

Cada execução cria uma pasta em:

```text
runtime/evidence/<run-id>/
```

com:

- screenshots por passo;
- `report.json`;
- `report.md`.

A pasta `runtime/` está no `.gitignore`.

## Segurança

Por defeito:

```env
QA_ALLOW_DANGEROUS_ACTIONS=false
QA_ENABLE_PAID_FALLBACK=false
```

O browser só navega para hosts definidos em `QA_ALLOWED_HOSTS`.

Se o login do MySANA redireccionar para um domínio SSO diferente, adiciona-o explicitamente:

```env
QA_ALLOWED_HOSTS=mysana.sanahotels.com,login.exemplo.pt
```

Não coloques cookies, passwords, screenshots de produção ou dados pessoais no GitHub.

## Próximo passo

A v0.2 deve gravar os workflows reais do MySANA, começando pelos ecrãs que mais repetes:

1. Processo Informacional;
2. Despesas (`dsform`);
3. Feiras e Eventos;
4. testes de persistência/reload;
5. validações negativas e casos limite.
