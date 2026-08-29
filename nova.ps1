param(
    [Parameter(Mandatory=$true, Position=0)]
    [string]$Prompt
)

$ErrorActionPreference = "Stop"

$ProfileName = if ($env:AWS_PROFILE) {
    $env:AWS_PROFILE
} else {
    "vyomquant-claude"
}

$Region = if ($env:AWS_REGION) {
    $env:AWS_REGION
} else {
    "ap-southeast-1"
}

$Model = "apac.amazon.nova-lite-v1:0"

$MessagesFile = Join-Path $env:TEMP "nova-agent-messages.json"
$InferenceFile = Join-Path $env:TEMP "nova-agent-inference.json"

$MessagesObject = @(
    @{
        role = "user"
        content = @(
            @{
                text = $Prompt
            }
        )
    }
)

$MessagesJson = ConvertTo-Json `
    -InputObject $MessagesObject `
    -Depth 10 `
    -Compress

$InferenceObject = @{
    maxTokens = 4000
    temperature = 0.1
}

$InferenceJson = ConvertTo-Json `
    -InputObject $InferenceObject `
    -Depth 10 `
    -Compress

[System.IO.File]::WriteAllText(
    $MessagesFile,
    $MessagesJson,
    [System.Text.UTF8Encoding]::new($false)
)

[System.IO.File]::WriteAllText(
    $InferenceFile,
    $InferenceJson,
    [System.Text.UTF8Encoding]::new($false)
)

Get-Content $MessagesFile -Raw | ConvertFrom-Json | Out-Null
Get-Content $InferenceFile -Raw | ConvertFrom-Json | Out-Null

$result = aws bedrock-runtime converse `
    --model-id $Model `
    --region $Region `
    --profile $ProfileName `
    --messages "file://$MessagesFile" `
    --inference-config "file://$InferenceFile" `
    --no-cli-pager

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$response = $result | ConvertFrom-Json

$response.output.message.content |
    Where-Object { $_.text } |
    ForEach-Object { $_.text }
