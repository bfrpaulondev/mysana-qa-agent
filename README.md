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

## Dashboard visual — Agent Chat + Computer Use

Depois do setup:

```powershell
.\.venv\Scripts\python.exe -m app.main dashboard
```

Abre localmente em `http://127.0.0.1:8765`.

### Fluxo principal

1. **Abrir Chromium e ir ao MySANA**.
2. Concluir o login assistido ou manual.
3. Escrever no **Agent Chat** o objectivo, por exemplo:
   `Entra nas despesas e testa os campos obrigatórios sem gravar nem apagar nada.`
4. O agente analisa a screenshot actual + DOM e gera um **plano proposto**.
5. O plano aparece no chat e em **Acções do agente**.
6. Só depois de **Executar plano** começa a execução.
7. A cada passo o agente:
   - tira nova screenshot;
   - lê os elementos interactivos actuais;
   - decide a próxima acção;
   - move o cursor e destaca o alvo;
   - aguarda aprovação no dashboard;
   - executa a acção aprovada;
   - observa novamente a página.

O plano inicial é apenas uma previsão. O agente não fica preso a uma sequência cega: se a interface mudar, volta a analisar a nova captura antes de decidir o próximo passo.

### Visão

O planner visual usa por defeito:

```env
QA_VISION_MODEL=nvidia_nim/z-ai/glm-5.3-flash
```

com a mesma `NVIDIA_NIM_API_KEY` usada pelo provider NVIDIA.

Se o modelo visual não estiver disponível, o agente pode cair para o planeamento baseado no DOM/texto.

### Login

Se o formulário de login for detectado, podes introduzir as credenciais no modal local ou digitá-las manualmente no Chromium.

As credenciais do modal não são guardadas no runtime, relatório, ficheiros ou GitHub.

Se os campos forem preenchidos mas o controlo de login não for accionado pelo detector normal, o agente visual inicia uma recuperação: observa a screenshot, procura o próximo passo lógico e propõe o clique correcto em vez de ficar parado.

### Aprovação por acção

Antes de qualquer clique, escrita ou selecção:

```text
Agente observa
      ↓
decide a próxima acção
      ↓
cursor vai até ao alvo
      ↓
alvo recebe borda/halo
      ↓
AGUARDAR APROVAÇÃO NO DASHBOARD
      ↓
Rejeitar | Aprovar e executar
```

Passwords nunca aparecem no painel de aprovação; apenas o comprimento do valor secreto.

### Aprovar sempre por tipo

No pedido de aprovação existem três opções:

- **Rejeitar**;
- **Aprovar uma vez**;
- **Sempre aprovar este tipo**.

As regras suportadas são:

- `CLICAR`;
- `ESCREVER`;
- `SELECCIONAR`.

Quando uma regra é activada, o dashboard mostra-a em **Regras de aprovação activas**, com botão **Desactivar**. As regras valem apenas durante a sessão Chromium actual e são limpas quando a sessão é fechada.

As protecções de segurança continuam acima destas regras: acções bloqueadas pela policy não passam a ser permitidas, e `ESCREVER` nunca auto-aprova passwords ou outros valores marcados como secretos.

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
