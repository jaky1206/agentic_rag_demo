from __future__ import annotations as _annotations

from dataclasses import dataclass
from dotenv import load_dotenv
import os

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.models.openai import OpenAIModel
from openai import AsyncOpenAI
from supabase import Client
from typing import List

load_dotenv()

llm = os.getenv('LLM_API_MODEL')
api_key=os.getenv("LLM_API_KEY")
model = OpenAIModel(model_name=llm, api_key=api_key)
scrape_target_name = os.getenv("SCRAP_TARGET_NAME")
scrape_target_base_url = os.getenv("SCRAP_TARGET_BASE_URL")

@dataclass
class WebsiteAgentDependencies:
    supabase: Client
    openai_client: AsyncOpenAI

system_prompt = f"""
You are an expert at {scrape_target_name} - a travel agent that you have access to all the documentation to,
to help potential customers answer their queries.

Your only job is to assist with this and you don't answer other questions besides describing what you are able to do.

Don't ask the user before taking an action, just do it. Always make sure you look at the documentation with the provided tools before answering the user's question unless you have already.

When you first look at the documentation, always start with RAG.
Then also always check the list of available documentation pages and retrieve the content of page(s) if it'll help.

Always let the user know when you didn't find the answer in the documentation or the right URL - be honest.
"""


site_expert = Agent(
    model,
    system_prompt=system_prompt,
    deps_type=WebsiteAgentDependencies,
    retries=2
)

async def get_embedding(text: str, openai_client: AsyncOpenAI) -> List[float]:
    """Get embedding vector."""
    try:
        response = await openai_client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        print(f"Error getting embedding: {e}")
        return [0] * 1536  # Return zero vector on error

@site_expert.tool
async def retrieve_relevant_documentation(ctx: RunContext[WebsiteAgentDependencies], user_query: str) -> str:
    """
    Retrieve relevant documentation chunks based on the query with RAG.
    
    Args:
        ctx: The context including the Supabase client and OpenAI client
        user_query: The user's question or query
        
    Returns:
        A formatted string containing the top 5 most relevant documentation chunks
    """
    try:
        # Get the embedding for the query
        query_embedding = await get_embedding(user_query, ctx.deps.openai_client)
        
        # Query Supabase for relevant documents
        result = ctx.deps.supabase.rpc(
            'match_knowledge_base',
            {
                'query_embedding': query_embedding,
                'match_count': 5,
                'filter': {'source': scrape_target_name}
            }
        ).execute()
        
        if not result.data:
            return "No relevant documentation found."
            
        # Format the results
        formatted_chunks = []
        for doc in result.data:
            chunk_text = f"""
# {doc['title']}

{doc['content']}
"""
            formatted_chunks.append(chunk_text)
            
        # Join all chunks with a separator
        return "\n\n---\n\n".join(formatted_chunks)
        
    except Exception as e:
        print(f"Error retrieving documentation: {e}")
        return f"Error retrieving documentation: {str(e)}"

@site_expert.tool
async def list_documentation_pages(ctx: RunContext[WebsiteAgentDependencies]) -> List[str]:
    """
    Retrieve a list of all available documentation pages.
    
    Returns:
        List[str]: List of unique URLs for all documentation pages
    """
    try:
        # Query Supabase for unique URLs where source is scrape_target_name
        result = ctx.deps.supabase.from_('knowledge_base') \
            .select('url') \
            .eq('metadata->>source', scrape_target_name) \
            .execute()
        
        if not result.data:
            return []
            
        # Extract unique URLs
        urls = sorted(set(doc['url'] for doc in result.data))
        return urls
        
    except Exception as e:
        print(f"Error retrieving documentation pages: {e}")
        return []

@site_expert.tool
async def get_page_content(ctx: RunContext[WebsiteAgentDependencies], url: str) -> str:
    """
    Retrieve the full content of a specific documentation page by combining all its chunks.
    
    Args:
        ctx: The context including the Supabase client
        url: The URL of the page to retrieve
        
    Returns:
        str: The complete page content with all chunks combined in order
    """
    try:
        # Query Supabase for all chunks of this URL, ordered by chunk_number
        result = ctx.deps.supabase.from_('knowledge_base') \
            .select('title, content, chunk_number') \
            .eq('url', url) \
            .eq('metadata->>source', scrape_target_name) \
            .order('chunk_number') \
            .execute()
        
        if not result.data:
            return f"No content found for URL: {url}"
            
        # Format the page with its title and all chunks
        page_title = result.data[0]['title'].split(' - ')[0]  # Get the main title
        formatted_content = [f"# {page_title}\n"]
        
        # Add each chunk's content
        for chunk in result.data:
            formatted_content.append(chunk['content'])
            
        # Join everything together
        return "\n\n".join(formatted_content)
        
    except Exception as e:
        print(f"Error retrieving page content: {e}")
        return f"Error retrieving page content: {str(e)}"
