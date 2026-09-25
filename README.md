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
2. `nvidia_nim/z-ai/glm-5.3`
3. `openai/gpt-5.6-luna` — apenas se `QA_ENABLE_PAID_FALLBACK=true`

Se Groq falhar ou atingir quota, o router tenta NVIDIA GLM-5.3. A OpenAI nunca é usada automaticamente
enquanto o fallback pago estiver desactivado.

## Configurar Groq + NVIDIA NIM sem expor chaves

Nunca edites `.env.example` com uma chave real.

Cria o teu ficheiro local:

```powershell
Copy-Item .env.example .env
```

Depois abre **apenas** o ficheiro `.env` e substitui:

```env
GROQ_API_KEY=YOUR_GROQ_API_KEY_HERE
NVIDIA_NIM_API_KEY=YOUR_NVIDIA_NIM_API_KEY_HERE
NVIDIA_NIM_API_BASE=https://integrate.api.nvidia.com/v1
```

pelas tuas chaves novas/reais localmente:

```env
GROQ_API_KEY=<chave-local>
NVIDIA_NIM_API_KEY=<chave-local>
NVIDIA_NIM_API_BASE=https://integrate.api.nvidia.com/v1
```

O ficheiro `.env` e variantes como `.env.local` estão no `.gitignore`.

Valida sem mostrar os segredos:

```powershell
.\.venv\Scripts\python.exe -m app.main doctor
```

Resultado esperado:

```text
Groq: configured
NVIDIA NIM: configured
OpenAI: missing
Secrets: hidden (doctor never prints API keys)
```

Depois testa realmente os dois providers com uma chamada mínima a cada um:

```powershell
.\.venv\Scripts\python.exe -m app.main providers-test
```

Exemplo:

```text
Provider connectivity test
One minimal request is sent to each configured free provider.
PASS | groq/openai/gpt-oss-120b | 420 ms | response='OK'
PASS | nvidia_nim/z-ai/glm-5.3 | 1250 ms | response='OK'
Secrets: hidden
```

O comando nunca imprime as API keys. Os tempos acima são apenas ilustrativos.

Enquanto:

```env
QA_ENABLE_PAID_FALLBACK=false
```

estiver definido, a OpenAI é ignorada mesmo que exista uma chave local.

## Instalação no Windows

Requisitos:

- Python 3.11
- Google Chrome
- Uma chave Groq e/ou NVIDIA NIM

PowerShell:

```powershell
.\scripts\setup.ps1
```

O script cria `.env` a partir de `.env.example` apenas se `.env` ainda não existir.

Depois:

```powershell
.\.venv\Scripts\python.exe -m app.main doctor
```

## Dashboard visual — modo Computer Use

Depois do setup:

```powershell
.\.venv\Scripts\python.exe -m app.main dashboard
```

Abre localmente em `http://127.0.0.1:8765`.

O botão **Abrir Chromium e ir ao MySANA** lança um browser Chromium visível. O agente mostra cada acção directamente na página:

- cursor virtual com animação até ao alvo;
- borda/halo forte no elemento que vai receber a acção;
- etiqueta fixa no topo: NAVEGAR, CLICAR, ESCREVER, SELECCIONAR, VALIDAR;
- feed das acções em tempo real no dashboard.

Se for detectado um campo de password, o dashboard pede o login. Podes:

1. introduzir utilizador/password no modal local e observar o agente escrever no Chromium; ou
2. escolher **Digitar manualmente no Chromium**.

As credenciais enviadas pelo modal são usadas apenas nessa chamada local a `127.0.0.1`; não são guardadas no runtime, relatório, ficheiros ou GitHub.

O primeiro **Iniciar teste visual** continua read-only: o cursor percorre até 10 elementos visíveis, destaca-os, tira screenshot e gera o relatório. Não clica nem preenche dados de negócio.

Para forçar o executável Chromium no Windows:

```env
QA_CHROMIUM_BINARY=C:\caminho\para\chromium.exe
```

Se ficar vazio, o projecto procura Chromium automaticamente e, se não encontrar, usa Chrome como browser Chromium-based.

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

Não coloques cookies, passwords, screenshots de produção, API keys ou dados pessoais no GitHub.

## Próximo passo

A v0.2 deve gravar os workflows reais do MySANA, começando pelos ecrãs que mais repetes:

1. Processo Informacional;
2. Despesas (`dsform`);
3. Feiras e Eventos;
4. testes de persistência/reload;
5. validações negativas e casos limite.
