#!/usr/bin/env python3
"""
Script to search existing products catalog in MongoDB by text search on search_string field.
Performs direct search with improved input string formatting:
- Splits camelCase format words
- Splits numbers from letters  
- Removes commas and semicolons
- Converts to lowercase
- Keeps spaces as separators
Results are stored in output file with input and scores.
"""

import json
import os
import sys
import argparse
import re
from datetime import datetime
from typing import Dict, Any, List

from utils import format_search_string, compute_rapidfuzz_score, extract_product_names, compute_given_name
from pinecone_integration import search_pinecone



def search_products_direct(collection, search_string: str, formatted_string: str) -> List[Dict[str, Any]]:
    """
    Search products using direct text search on the formatted input string.
    
    Args:
        collection: MongoDB collection object
        search_string: The original search string
        formatted_string: The formatted search string to use for search
        
    Returns:
        List of matching products with scores
    """
    try:
        # Use MongoDB text search with scoring
        # Create text index if it doesn't exist (MongoDB will ignore if exists)
        try:
            collection.create_index([("search_string", "text")])
        except Exception:
            pass  # Index might already exist
        
        # Perform text search with scoring using the formatted string
        results = list(collection.find(
            {"$text": {"$search": formatted_string}},
            {"score": {"$meta": "textScore"}}
        ).sort([("score", {"$meta": "textScore"})]).limit(50))
        
        return results
    except Exception as e:
        print(f"Error in direct search: {e}")
        return []


def apply_rapidfuzz_scoring(search_string: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Apply RapidFuzz scoring to search results and sort by RapidFuzz score.
    
    Args:
        search_string: The original search string
        results: List of MongoDB search results
        
    Returns:
        List of results with RapidFuzz scores, sorted by RapidFuzz score descending
    """
    if not results:
        return results
    
    # Add RapidFuzz scores to each result
    for result in results:
        rapidfuzz_score = compute_rapidfuzz_score(search_string, result)
        result['rapidfuzz_score'] = rapidfuzz_score
    
    # Sort by RapidFuzz score in descending order
    results.sort(key=lambda x: x.get('rapidfuzz_score', 0), reverse=True)
    
    return results



def search_products(search_string: str) -> Dict[str, Any]:
    """
    Main search function that performs both MongoDB and Pinecone searches.
    
    Args:
        search_string: The input search string
        
    Returns:
        Dictionary containing search results and metadata
    """
    try:
        from pymongo import MongoClient
        from pymongo.errors import ConnectionFailure, ConfigurationError
        
        # Get MongoDB URI from environment variable
        mongo_uri = os.getenv('MONGO_URI')
        if not mongo_uri:
            print("Error: MONGO_URI environment variable not set")
            print("Please set the MongoDB connection URI in the MONGO_URI environment variable")
            return {"error": "MONGO_URI not set"}
        
        # Initialize MongoDB connection
        try:
            print(f"Connecting to MongoDB...")
            client = MongoClient(mongo_uri)
            # Test connection
            client.admin.command('ping')
            db = client.get_database()  # Use default database from URI
            collection = db['products-catalog']
            print("Successfully connected to MongoDB")
        except (ConnectionFailure, ConfigurationError) as e:
            print(f"Error connecting to MongoDB: {e}")
            return {"error": f"MongoDB connection failed: {e}"}
        
        # Format the search string
        formatted_string = format_search_string(search_string)
        print(f"Original input: '{search_string}'")
        print(f"Formatted input: '{formatted_string}'")
        
        # Perform direct search with formatted string
        print("Performing direct search with formatted input...")
        direct_results = search_products_direct(collection, search_string, formatted_string)
        
        # Add given_name field to direct results
        for result in direct_results:
            result['given_name'] = compute_given_name(result)
        
        # Apply RapidFuzz scoring to the results
        print("Computing RapidFuzz scores and resorting results...")
        direct_results_with_rapidfuzz = apply_rapidfuzz_scoring(search_string, direct_results.copy())
        
        # Add given_name field to rapidfuzz results 
        for result in direct_results_with_rapidfuzz:
            result['given_name'] = compute_given_name(result)
        
        # Perform Pinecone search
        print("Performing Pinecone search...")
        pinecone_results = search_pinecone(search_string, top_k=10)
        
        # Prepare results
        results = {
            "timestamp": datetime.now().isoformat(),
            "input_string": search_string,
            "formatted_string": formatted_string,
            "direct_search": {
                "count": len(direct_results),
                "results": direct_results
            },
            "rapidfuzz_search": {
                "count": len(direct_results_with_rapidfuzz),
                "results": direct_results_with_rapidfuzz
            },
            "pinecone_search": {
                "count": len(pinecone_results),
                "results": pinecone_results
            }
        }
        
        # Close MongoDB connection
        client.close()
        
        print(f"Direct search found {len(direct_results)} results")
        print(f"RapidFuzz scoring applied to {len(direct_results_with_rapidfuzz)} results")
        print(f"Pinecone search found {len(pinecone_results)} results")
        
        return results
        
    except ImportError:
        error_msg = "Required packages not installed. Please run: pip install -r requirements.txt"
        print(error_msg)
        return {"error": error_msg}
    except Exception as e:
        error_msg = f"Error during search: {e}"
        print(error_msg)
        return {"error": error_msg}



def save_results(results: Dict[str, Any], output_file: str = None) -> str:
    """
    Save search results to a JSON file.
    
    Args:
        results: Search results dictionary
        output_file: Optional output filename
        
    Returns:
        The filename where results were saved
    """
    if not output_file:
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_input = re.sub(r'[^\w\s-]', '', results.get('input_string', 'search')).strip()
        safe_input = re.sub(r'[-\s]+', '_', safe_input)[:30]  # Limit length
        output_file = f"search_results_{safe_input}_{timestamp}.json"
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"Results saved to: {output_file}")
        return output_file
        
    except Exception as e:
        print(f"Error saving results: {e}")
        return ""


def main():
    """Main function to handle command line arguments and execute search."""
    parser = argparse.ArgumentParser(description='Search products catalog by text search on search_string field')
    parser.add_argument('search_string', help='Text to search for in product catalog')
    parser.add_argument('-o', '--output', help='Output file path (optional)')
    
    args = parser.parse_args()
    
    if not args.search_string.strip():
        print("Error: Search string cannot be empty")
        sys.exit(1)
    
    print("Product Catalog Search Tool")
    print("=" * 40)
    print(f"Search string: {args.search_string}")
    print()
    
    # Perform search
    results = search_products(args.search_string)
    
    # Check for errors
    if "error" in results:
        print(f"Search failed: {results['error']}")
        sys.exit(1)
    
    # Save results
    output_file = save_results(results, args.output)
    
    # Print summary
    print("\nSearch Summary:")
    print(f"- Formatted input: '{results['formatted_string']}'")
    print(f"- Direct search: {results['direct_search']['count']} results")
    print(f"- RapidFuzz search: {results['rapidfuzz_search']['count']} results")
    print(f"- Pinecone search: {results['pinecone_search']['count']} results")
    print(f"- Results saved to: {output_file}")
    
    # Print top 10 direct search results
    if results['direct_search']['results']:
        print("\nTop 10 direct search results (MongoDB scoring):")
        for i, result in enumerate(results['direct_search']['results'][:10]):
            score = result.get('score', 0)
            product_id = result.get('_id', 'Unknown')
            
            # Extract unique product names
            unique_product_names = extract_product_names(result.get('product_name', []))
            
            # Get other requested fields
            quantity = result.get('quantity', '')
            brands = result.get('brands', '')
            categories = result.get('categories', [])
            labels = result.get('labels', [])
            
            print(f"  {i+1}. MongoDB Score: {score:.2f} - ID: {product_id}")
            print(f"     Given Name: {result.get('given_name', 'N/A')}")
            print(f"     Product Names: {', '.join(unique_product_names) if unique_product_names else 'N/A'}")
            print(f"     Quantity: {quantity if quantity else 'N/A'}")
            print(f"     Brands: {brands if brands else 'N/A'}")
            print(f"     Categories: {', '.join(categories) if categories else 'N/A'}")
            print(f"     Labels: {', '.join(labels) if labels else 'N/A'}")
            print(f"     Text: {result.get('search_string', '')}")
            print()
    
    # Print top 10 RapidFuzz results
    if results['rapidfuzz_search']['results']:
        print("\nTop 10 RapidFuzz search results (Custom relevance scoring):")
        for i, result in enumerate(results['rapidfuzz_search']['results'][:10]):
            mongo_score = result.get('score', 0)
            rapidfuzz_score = result.get('rapidfuzz_score', 0)
            product_id = result.get('_id', 'Unknown')
            
            # Extract unique product names
            unique_product_names = extract_product_names(result.get('product_name', []))
            
            # Get other requested fields
            quantity = result.get('quantity', '')
            brands = result.get('brands', '')
            categories = result.get('categories', [])
            labels = result.get('labels', [])
            
            print(f"  {i+1}. RapidFuzz Score: {rapidfuzz_score:.2f} (MongoDB: {mongo_score:.2f}) - ID: {product_id}")
            print(f"     Given Name: {result.get('given_name', 'N/A')}")
            print(f"     Product Names: {', '.join(unique_product_names) if unique_product_names else 'N/A'}")
            print(f"     Quantity: {quantity if quantity else 'N/A'}")
            print(f"     Brands: {brands if brands else 'N/A'}")
            print(f"     Categories: {', '.join(categories) if categories else 'N/A'}")
            print(f"     Labels: {', '.join(labels) if labels else 'N/A'}")
            print(f"     Text: {result.get('search_string', '')}")
            print()
    
    # Print top 10 Pinecone results
    if results['pinecone_search']['results']:
        print("\nTop 10 Pinecone search results (Semantic similarity scoring):")
        for i, result in enumerate(results['pinecone_search']['results'][:10]):
            score = result.get('score', 0)
            result_id = result.get('id', 'Unknown')
            given_name = result.get('given_name', 'N/A')
            text = result.get('text', 'N/A')
            
            print(f"  {i+1}. Pinecone Score: {score:.4f} - ID: {result_id}")
            print(f"     Given Name: {given_name}")
            print(f"     Text: {text}")
            
            # Display additional product metadata if available
            metadata = result.get('metadata', {})
            if 'product_names' in metadata:
                # This is a product result
                product_names = metadata.get('product_names', [])
                quantity = metadata.get('quantity', '')
                brands = metadata.get('brands', '')
                categories = metadata.get('categories', [])
                labels = metadata.get('labels', [])
                
                if product_names:
                    print(f"     Product Names: {', '.join(product_names)}")
                if quantity:
                    print(f"     Quantity: {quantity}")
                if brands:
                    print(f"     Brands: {brands}")
                if categories:
                    print(f"     Categories: {', '.join(categories) if isinstance(categories, list) else categories}")
                if labels:
                    print(f"     Labels: {', '.join(labels) if isinstance(labels, list) else labels}")
            
            print()


if __name__ == "__main__":
    main()