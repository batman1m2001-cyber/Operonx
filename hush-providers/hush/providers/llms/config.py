from enum import Enum
from typing import ClassVar, Optional, Sequence, Union, Dict
from hush.core.utils import YamlModel
from pathlib import Path

class LLMType(Enum):
    OPENAI = "openai"
    AZURE = "azure"
    VLLM = "vllm"
    GEMINI = "gemini"


class CompletionConfig(YamlModel):
    """LLM completion request parameters

    Parameters:
        temperature: Controls randomness in response generation (0.0 = deterministic)
        top_p: Nucleus sampling parameter controlling response diversity
        n: Number of completions to generate
        stream: Whether to stream responses back as they are generated
        stop: Sequence(s) at which to stop generating
        max_tokens: Maximum number of tokens to generate
        frequency_penalty: Penalty for using frequent tokens
        presence_penalty: Penalty for token repetition
        response_format: Format specification for the response
    """
    temperature: float = 0.0
    top_p: float = 0.1
    n: Optional[int] = None
    stream: bool = True
    stop: Optional[Union[str, Sequence[str]]] = None
    max_tokens: Optional[int] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    response_format: Optional[dict] = None


class LLMConfig(YamlModel):
    """Base configuration for LLM Interactions

    Parameters:
        api_type: Type of LLM API to use
        completion: Configuration parameters for completion requests
        proxy (str | None, optional): Proxy URL for requests. Defaults to None.
        cost_per_input_token: Cost in USD per input token (for manual cost tracking)
        cost_per_output_token: Cost in USD per output token (for manual cost tracking)
    """
    _category: ClassVar[str] = "llm"

    api_type: LLMType
    proxy: str | None = None
    # Cost per token in USD (for gateways/models without automatic pricing)
    cost_per_input_token: float | None = None
    cost_per_output_token: float | None = None


    @classmethod
    def create_config(cls, config_data: Dict) -> "LLMConfig":
        """Create appropriate LLM config based on the api_type

        Parameters:
            config_data: Dictionary containing configuration data including api_type

        Returns:
            An instance of the appropriate LLMConfig subclass

        Raises:
            ValueError: If api_type is not recognized or missing
        """
        if "api_type" not in config_data:
            raise ValueError("api_type is required in config_data")

        api_type_value = config_data.get("api_type")
        # Handle both string and enum values
        if isinstance(api_type_value, str):
            try:
                api_type = LLMType(api_type_value)
            except ValueError:
                raise ValueError(f"Unsupported api_type: {api_type_value}")
        elif isinstance(api_type_value, LLMType):
            api_type = api_type_value
        else:
            raise ValueError(f"api_type must be a string or LLMType enum, got {type(api_type_value)}")

        # Create the appropriate config based on api_type
        if api_type == LLMType.OPENAI:
            return OpenAIConfig(**config_data)
        elif api_type == LLMType.AZURE:
            return AzureConfig(**config_data)
        elif api_type == LLMType.GEMINI:
            return GeminiConfig(**config_data)
        elif api_type == LLMType.VLLM:
            # Assuming VLLM uses same structure as OpenAI
            return OpenAIConfig(**config_data)
        else:
            raise ValueError(f"Unsupported api_type: {api_type}")

    @classmethod
    def from_yaml_file(cls, file_path: Path) -> "LLMConfig":
        """Read yaml file and return appropriate LLMConfig instance based on api_type

        Overrides the parent method to return the correct LLMConfig subclass

        Parameters:
            file_path: Path to the YAML configuration file

        Returns:
            An instance of the appropriate LLMConfig subclass
        """
        config_data = cls.read_yaml(file_path)
        return cls.create_config(config_data)


class OpenAIConfig(LLMConfig):
    """Configuration for OpenAI-compatible endpoints

    Parameters:
        api_key: Authentication key for the OpenAI API
        base_url: Base URL for API requests
        model: Name of the model to use
        batch_size: Maximum requests per batch job (default: 50000, OpenAI limit)
        batch_flush_interval: Seconds before auto-flushing pending batch requests (default: 60.0)
        batch_poll_interval: Seconds between batch status checks (default: 30.0)
        batch_timeout: Maximum wait time for batch completion in seconds (default: 86400 = 24h)
    """
    api_type: LLMType = LLMType.OPENAI  # Auto-assigned
    api_key: str
    base_url: str
    model: str
    # Batch API configuration
    batch_size: int = 50000
    batch_flush_interval: float = 60.0
    batch_poll_interval: float = 30.0
    batch_timeout: float = 86400.0


class AzureConfig(LLMConfig):
    """Configuration for Azure-compatible endpoints

    Parameters:
        api_key: Authentication key for the Azure API
        api_version: Version of the Azure API to use
        azure_endpoint: Base URL for Azure API requests
        model: Name of the deployment to use
    """
    api_type: LLMType = LLMType.AZURE  # Auto-assigned
    api_key: str
    api_version: str
    azure_endpoint: str
    model: str


class GeminiConfig(LLMConfig):
    """Configuration for Gemini-compatible endpoints

    Parameters:
        project_id: Google Cloud project identifier
        private_key_id: ID of the service account private key
        private_key: Service account private key in PEM format
        client_email: Service account email address
        client_id: Service account client ID
        auth_uri: OAuth2 authorization endpoint
        token_uri: OAuth2 token endpoint
        auth_provider_x509_cert_url: X.509 certificate URL for the auth provider
        client_x509_cert_url: X.509 certificate URL for the client
        universe_domain: Google API domain
        location: Google Cloud region for Gemini API
        model_name: Gemini model version to use
    """
    api_type: LLMType = LLMType.GEMINI  # Auto-assigned
    project_id: str
    private_key_id: str
    private_key: str
    client_email: str
    client_id: str
    auth_uri: str = "https://accounts.google.com/o/oauth2/auth"
    token_uri: str = "https://oauth2.googleapis.com/token"
    auth_provider_x509_cert_url: str = "https://www.googleapis.com/oauth2/v1/certs"
    client_x509_cert_url: str
    universe_domain: str = "googleapis.com"

    # Gemini model configuration
    location: str = "us-central1"
    model: str = "gemini-2.0-flash-001"
