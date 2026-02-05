# -*- coding: utf-8 -*-

from __future__ import annotations
from typing import Any, Optional, List, Dict

import os
import json
import requests
import aiohttp
import asyncio

from aiohttp.client_exceptions import ClientError

from hush.providers.rerankers.config import RerankingConfig

from .base import BaseReranker

from pydantic import BaseModel, Field
from typing import List, Optional, Union


class Usage(BaseModel):
    total_tokens: int
    

class Document(BaseModel):
    text: str
    

class RerankerResult(BaseModel):
    index: int
    document: Document
    relevance_score: float = Field(description="Relevance score for the document")
    

class VLLMRerankerResponse(BaseModel):
    """
    Pydantic model for VLLM reranker API response
    
    Attributes:
        id (str): Unique identifier for the reranking request
        model (str): Model used for reranking
        usage (Usage): Token usage information
        results (List[RerankerResult]): List of reranked documents with their scores
    """
    id: str = Field(description="Unique identifier for the reranking request")
    # model: str = Field(description="Model used for reranking")
    # usage: Usage
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


class VLLMReranker(BaseReranker):
    r"""Provides text reranking functionalities using VLLM Embedding Inference."""
    
    def __init__(
        self,
        config: RerankingConfig
    ) -> None:
        
        """Initialize the VLLM embedding client with the provided configuration."""
        
        self.config = config
        
        if not self.config.base_url:
            raise ValueError("base_url is required in the configuration")
        
        # Set up default headers
        self.default_headers = {
            'Content-Type': 'application/json'
        }
        
        if self.config.api_key:
            self.default_headers['Authorization'] = f'Bearer {self.config.api_key}'
            
    async def run(
        self,
        query: str,
        texts: List[str],
        top_k: int = None,
        threshold: float = None,
        **kwargs: Any
    ) -> List[Dict]:
        if not texts:
            return []
        
        try:
            # 1. send request with input payload to get the ranked results
            payload = json.dumps({
                "model": self.config.model,
                "query": query,
                "documents": texts
            })
                        
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.config.base_url, 
                    headers=self.default_headers, 
                    data=payload,
                    timeout=aiohttp.ClientTimeout(total=30),
                    **kwargs
                ) as response:
                    response.raise_for_status()
                    result = await response.json()
            
            
            reranker_response = VLLMRerankerResponse.model_validate(result)
            
            return reranker_response.export(
                top_k=top_k,
                threshold=threshold,
                export_json=True
            )
            
        except ClientError as e:
            raise ConnectionError(f"Failed to connect to VLLM server: {str(e)}") from e
        except asyncio.TimeoutError as e:
            raise ConnectionError("Request timed out") from e
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            raise ValueError(f"Invalid response format: {str(e)}") from e
        

if __name__ == "__main__":    
    # Example usage
    async def main():
        from hush.providers.rerankers.config import RerankingType

        import time
        
        reranker = VLLMReranker(RerankingConfig.default())
        
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
            query = query,
            texts = original_texts
        )
        
        for r in results:
            print(r)
            
        execution_time = time.time() - start_time
        print(f"\nExecution time: {execution_time:.4f} seconds")
    
    asyncio.run(main())