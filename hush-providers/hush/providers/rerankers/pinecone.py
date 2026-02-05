# -*- coding: utf-8 -*-

from __future__ import annotations
from typing import Any, Optional, List, Dict

import json
import aiohttp
import asyncio

from aiohttp.client_exceptions import ClientError

from hush.providers.rerankers.config import RerankingConfig

from .base import BaseReranker

from pydantic import BaseModel, Field
from typing import List, Optional


class Usage(BaseModel):
    total_tokens: int


class Document(BaseModel):
    text: str


class RerankerResult(BaseModel):
    index: int
    document: Document
    relevance_score: float = Field(description="Relevance score for the document")


class PineconeRawResponse(BaseModel):
    """Internal model for Pinecone API response structure"""
    model: str
    data: List[Dict]
    usage: Dict


class VLLMRerankerResponse(BaseModel):
    """
    Pydantic model for reranker API response (compatible format)
    
    Attributes:
        id (str): Unique identifier for the reranking request
        results (List[RerankerResult]): List of reranked documents with their scores
    """
    id: str = Field(description="Unique identifier for the reranking request")
    results: List[RerankerResult]
    
    def export(
        self,
        top_k: int = None,
        threshold: float = None,
        export_json=True
    ) -> List[RerankerResult]:
        """Filter and optionally export results to JSON format.

        Args:
            top_k (int): Maximum number of results to return. Defaults to 3.
            threshold (float): Minimum score threshold. Defaults to 0.0.
            export_json (bool): Whether to return dict format. Defaults to True.

        Returns:
            Union[List[Dict[str, Union[int, float]]], List[RerankerResult]]: 
                Filtered results in dict or model format.
        """
        top_k_results = self.results[:top_k] if top_k else self.results
        threshold_results = [
            r for r in top_k_results
            if r.relevance_score > threshold
        ] if threshold else top_k_results
        
        if export_json:
            return [
                {"index": r.index, "score": r.relevance_score}
                for r in threshold_results
            ]
        else:
            return threshold_results


class PineconeReranker(BaseReranker):
    r"""Provides text reranking functionalities using Pinecone Rerank API."""
    
    def __init__(
        self,
        config: RerankingConfig
    ) -> None:
        """Initialize the Pinecone reranker client with the provided configuration."""
        
        self.config = config
        
        # Set default base URL if not provided
        if not self.config.base_url:
            self.config.base_url = "https://api.pinecone.io/rerank"
        
        if not self.config.api_key:
            raise ValueError("api_key is required for Pinecone Reranker")
        
        # Set up Pinecone-specific headers
        self.default_headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Api-Key': self.config.api_key,
            'X-Pinecone-API-Version': '2025-04'
        }
    
    async def run(
        self,
        query: str,
        texts: List[str],
        top_k: int = None,
        threshold: float = None,
        return_documents: bool = True,
        truncate: str = "END",
        **kwargs: Any
    ) -> List[Dict]:
        """
        Rerank documents using Pinecone Rerank API.
        
        Args:
            query (str): The query text to rerank documents against
            texts (List[str]): List of document texts to rerank
            top_k (int): Maximum number of results to return. Defaults to None (all results).
            threshold (float): Minimum score threshold. Defaults to None.
            return_documents (bool): Whether to return document content. Defaults to True.
            truncate (str): Truncation strategy ("END" or "NONE"). Defaults to "END".
            **kwargs: Additional arguments to pass to aiohttp session
            
        Returns:
            List[Dict]: List of reranked results with index and score (same format as VLLMReranker)
        """
        if not texts:
            return []
        
        try:
            # Prepare documents in Pinecone format
            documents = [
                {"id": f"doc_{i}", "text": text} 
                for i, text in enumerate(texts)
            ]
            
            # Build payload
            payload = {
                "model": self.config.model,
                "query": query,
                "documents": documents,
                "return_documents": return_documents,
                "top_n": top_k if top_k else len(texts),
                "parameters": {
                    "truncate": truncate
                }
            }
            
            # Make async request
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.base_url,
                    headers=self.default_headers,
                    data=json.dumps(payload),
                    timeout=aiohttp.ClientTimeout(total=30),
                    **kwargs
                ) as response:
                    response.raise_for_status()
                    result = await response.json()
            
            # Convert Pinecone response to VLLMRerankerResponse format
            reranker_results = []
            for item in result.get("data", []):
                # Get the original text from the input texts using the index
                original_index = item["index"]
                original_text = texts[original_index]
                
                reranker_results.append(
                    RerankerResult(
                        index=original_index,
                        document=Document(text=original_text),
                        relevance_score=item["score"]
                    )
                )
            
            # Create compatible response object
            reranker_response = VLLMRerankerResponse(
                id=result.get("id", "pinecone-rerank"),
                results=reranker_results
            )
            
            # Export results in the same format as VLLMReranker
            return reranker_response.export(
                top_k=top_k,
                threshold=threshold,
                export_json=True
            )
            
        except ClientError as e:
            raise ConnectionError(f"Failed to connect to Pinecone server: {str(e)}") from e
        except asyncio.TimeoutError as e:
            raise ConnectionError("Request timed out") from e
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ValueError(f"Invalid response format: {str(e)}") from e


if __name__ == "__main__":
    # Example usage
    async def main():
        from hush.providers.rerankers.config import RerankingConfig
        import time
        
        # Configure Pinecone reranker
        config = RerankingConfig.default()
        
        reranker = PineconeReranker(config)
        
        original_texts = [
            """File tài liệu: Quy định về giao dịch chuyển tiền nhanh 247
Câu hỏi: Thời gian xử lý tra soát giao dịch chuyển 
tiền nhanh 247 qua Napas là bao lâu?
Câu trả lời: Tối đa 04 ngày làm việc (không tính thứ 7, chủ nhật và ngày nghỉ lễ)""",

            """File tài liệu: Hướng dẫn giao dịch ngân hàng điện tử
Câu hỏi: Phí giao dịch chuyển tiền nhanh 247 là bao nhiêu?
Câu trả lời: Miễn phí cho giao dịch dưới 500.000 VND, 11.000 VND cho giao dịch trên 500.000 VND""",

            """File tài liệu: Chính sách khách hàng ưu tiên
Câu hỏi: Khách hàng VIP được ưu đãi gì khi chuyển tiền 247?
Câu trả lời: Miễn phí toàn bộ giao dịch chuyển tiền 247, không giới hạn số tiền""",

            """File tài liệu: Quy trình xử lý khiếu nại
Câu hỏi: Quy trình xử lý tra soát giao dịch lỗi là gì?
Câu trả lời: Tiếp nhận yêu cầu, xác minh thông tin trong 24h, phản hồi kết quả trong 04 ngày làm việc""",

            """File tài liệu: Hạn mức giao dịch
Câu hỏi: Hạn mức chuyển tiền 247 qua Napas là bao nhiêu?
Câu trả lời: Tối đa 500 triệu VND/giao dịch và 2 tỷ VND/ngày""",

            """File tài liệu: Thời gian hoạt động dịch vụ
Câu hỏi: Thời gian hoạt động của dịch vụ chuyển tiền 247?
Câu trả lời: Hoạt động 24/7, kể cả ngày nghỉ và ngày lễ""",

            """File tài liệu: Hướng dẫn tra soát giao dịch
Câu hỏi: Cách thức gửi yêu cầu tra soát giao dịch?
Câu trả lời: Khách hàng có thể gửi yêu cầu qua email, tổng đài 24/7 hoặc trực tiếp tại quầy""",

            """File tài liệu: Quy định về bằng chứng giao dịch
Câu hỏi: Giấy báo có được cung cấp như thế nào?
Câu trả lời: Được cung cấp qua email trong vòng 04 ngày làm việc sau khi nhận yêu cầu""",

            """File tài liệu: Chính sách hoàn tiền
Câu hỏi: Thời gian hoàn tiền cho giao dịch lỗi?
Câu trả lời: Tối đa 07 ngày làm việc sau khi có kết quả tra soát""",

            """File tài liệu: Quy định về xác thực giao dịch
Câu hỏi: Phương thức xác thực giao dịch chuyển tiền 247?
Câu trả lời: Soft OTP hoặc Smart OTP, tùy theo đăng ký của khách hàng"""
        ]
        
        query = "Thời gian xử lý và cung cấp giấy báo có cho giao dịch chuyển tiền nhanh 247 qua Napas là bao lâu?"
        
        start_time = time.time()

        results = await reranker.run(
            query=query,
            texts=original_texts,
            top_k=5,
            threshold=0.5
        )
        
        print(f"Top {len(results)} results:")
        for r in results:
            print(f"Index: {r['index']}, Score: {r['score']:.4f}")
            print(r)
            
        execution_time = time.time() - start_time
        print(f"\nExecution time: {execution_time:.4f} seconds")
    
    asyncio.run(main())