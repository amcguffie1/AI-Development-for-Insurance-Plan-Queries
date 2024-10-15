import asyncio
import base64
import json
import os
import re
import traceback
import nest_asyncio
import requests
from bs4 import BeautifulSoup
from openai import OpenAI
import streamlit as st

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()

# Initialize OpenAI client
# Access the OpenAI API key and Proxy API key securely using Streamlit secrets
openai_api_key = st.secrets["api_keys"]["openai_api_key"]
proxy_api_key = st.secrets["api_keys"]["proxy_api_key"]

# Initialize the OpenAI client with the secure API key
client = OpenAI(api_key=openai_api_key)

# Global thread initialization
if 'user_thread' not in st.session_state:
    st.session_state.user_thread = client.beta.threads.create()

# Proxy setup
PROXY_URL = 'https://proxy.scrapeops.io/v1/'
API_KEY = proxy_api_key

# Function to create an assistant and save its ID
def get_or_create_assistant():
    """Check if the assistant ID is hardcoded or create a new one."""
    # Hardcoded assistant ID
    assistant_id = "asst_pkUM4L6RKmVJMBMpPGinrrKs"
    
    if assistant_id:
        st.sidebar.success(f"Using existing assistant with ID: {assistant_id}")
    else:
        try:
            with st.spinner("Creating a new assistant..."):
                assistant = client.beta.assistants.create(
                    name="ProviderFetcher",
                    instructions=instructions,
                    model="gpt-4o-mini",
                    tools=tools
                )
            assistant_id = assistant.id
            st.sidebar.success(f"Assistant created successfully with ID: {assistant_id}")
        except Exception as e:
            st.error(f"Failed to create assistant: {e}")
            assistant_id = None
    
    return assistant_id


def fetch_html_via_proxy(target_url):
    """Fetches HTML from the target URL using the proxy service."""
    params = {
        'api_key': API_KEY,
        'url': target_url,
        'render_js': 'false',
        'residential': 'true',
    }
    try:
        response = requests.get(PROXY_URL, params=params)
        response.encoding = 'utf-8'  # Enforce UTF-8 encoding

        if response.status_code == 200:
            return BeautifulSoup(response.text, 'html.parser')
        else:
            st.error(f"Failed to fetch the page: {target_url}, status code: {response.status_code}")
            return None
    except requests.exceptions.RequestException as e:
        st.error(f"An error occurred while fetching {target_url}: {e}")
        return None


def scrape_content(url):
    """Fetches HTML from the target URL using the proxy service, extracts text content, and deduplicates href links."""
    params = {
        'api_key': API_KEY,
        'url': url,
        'render_js': 'true',
        'residential': 'true',
    }
    
    try:
        with st.spinner(f"Scraping content from {url}..."):
            # Make the request to the proxy service
            response = requests.get(PROXY_URL, params=params)
            response.encoding = 'utf-8'  # Enforce UTF-8 encoding
            
            # Check if the request was successful
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Extract and clean the text content
                content = soup.get_text(separator="\n", strip=True)
                
                # Extract and deduplicate href links using set comprehension
                links = sorted({a.get('href') for a in soup.find_all('a', href=True) if a.get('href')})
                
                st.success(f"Successfully scraped content from {url}")
                return {
                    'content': content,
                    'links': links  # Return the sorted list of links
                }
            else:
                st.error(f"Failed to fetch the page: {url}, status code: {response.status_code}")
                return None
    except requests.exceptions.Timeout:
        st.error(f"Request timed out while trying to scrape {url}.")
        return None
    except requests.exceptions.TooManyRedirects:
        st.error(f"Too many redirects while trying to scrape {url}.")
        return None
    except requests.exceptions.RequestException as e:
        st.error(f"An error occurred while scraping {url}: {e}")
        return None

def extract_data(soup):
    """Extracts provider details from the HTML soup and returns the data as a list of dictionaries."""
    listings = soup.find_all('div', class_='directorist-listing-single__content')

    # Use list comprehension to extract data from listings
    results = [
        {
            **(
                {
                    'href': (link_tag := listing.find_previous_sibling('div', class_='directorist-listing-single__header').find('a')).get('href'),
                    'name': link_tag.text.strip()
                } if listing.find_previous_sibling('div', class_='directorist-listing-single__header') else {}
            ),
            **{
                detail_key: text_div.text.strip()
                for li in listing.find('div', class_='directorist-listing-single__info--list').find_all('li')
                if (text_div := li.find('div', class_='directorist-listing-card-text')) and text_div.i
                for detail_key, icon_check in [
                    ('clinic', "comment-solid"),
                    ('address', "map-marker-solid"),
                    ('NPI', "comment-solid")
                ]
                if icon_check in text_div.i.get('style', '') and (
                    (detail_key == 'NPI' and "NPI" in text_div.text.strip()) or 
                    (detail_key != 'NPI' and "NPI" not in text_div.text.strip())
                )
            },
            **{
                'phone': phone_div.a.text.strip()
                for li in listing.find('div', class_='directorist-listing-single__info--list').find_all('li')
                if (phone_div := li.find('div', class_='directorist-listing-card-phone')) and phone_div.a
            },
            **{
                'accepting_patients': select_div.text.split(":")[1].strip()
                for li in listing.find('div', class_='directorist-listing-single__info--list').find_all('li')
                if (select_div := li.find('div', class_='directorist-listing-card-select')) and "check-circle-solid" in select_div.i.get('style', '')
            }
        }
        for listing in listings
    ]
    return results


def scrape_provider_search(url):
    """Scrapes provider search results from the given URL and returns the data in JSON format."""
    st.info(f"Scraping provider search results from {url}...")
    soup = fetch_html_via_proxy(url)
    if soup:
        extracted_data = extract_data(soup)
        if extracted_data:
            st.success("Successfully scraped provider search results.")
            st.json(extracted_data)
            return json.dumps(extracted_data, ensure_ascii=False, indent=2)
        else:
            st.warning(f"No provider search results found at {url}.")
    else:
        st.error(f"Failed to fetch provider search results from {url}.")
    return json.dumps({"error": "Failed to scrape provider search results."}, ensure_ascii=False, indent=2)

def safe_tool_call(func, tool_name, **kwargs):
    """Safely execute a tool call and handle exceptions."""
    try:
        result = func(**kwargs)
        return result if result is not None else f"No content returned from {tool_name}"
    except Exception as e:
        st.error(f"Error in {tool_name}: {str(e)}")
        return f"Error occurred in {tool_name}: {str(e)}"

# Define function specifications for content scraping
tools = [
    {
        "type": "function",
        "function": {
            "name": "scrape_content",
            "description": "Use this function to scrape text content from a given URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to scrape content from."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "scrape_provider_search",
            "description": "Use this function to scrape provider search results from a given URL, and return the data in JSON format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The provider search URL to scrape listings from."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {"type": "code_interpreter"}
]

available_functions = {
    "scrape_content": scrape_content,
    "scrape_provider_search": scrape_provider_search
}

# Instructions for the assistant
instructions = """
General Guidance:
For all questions unrelated to web scraping, the model should answer solely based on the knowledge files uploaded. If the user asks a question not covered in the uploaded files, the model should inform the user that it could not find a relevant answer and ask for permission before searching online. The model should never perform a web search without user confirmation, unless it explicitly involves provider data collection or scraping tasks.

Web Scraping Tasks:
For provider-related questions, the model should focus on scraping the required data as per the instructions. You are responsible for scraping provider data from given websites, focusing on obtaining complete information for each provider. Use the scraping functions and search results to gather the necessary links.

Specific Roles:
General Questions on Insurance Plans: Answer based only on the knowledge files uploaded.
Provider Data Scraping: Execute web scraping using the scrape_provider_search and scrape_content functions following the detailed steps below.

Key Information to Collect:
Present the following information for each provider in a table format:
- Name
- Clinic
- Address
- Phone
- NPI (National Provider Identifier)
- Accepting New Patients status
- Link (href)

Data Collection Process:
Primary Search: Always start by using the scrape_provider_search function with the Sonder Health Plans website.
Fallback Method: If no results are found, use the scrape_content function to perform a Google search for the correct Sonder Health Plans links. Always use this when the results are limited.
Deep Search: Use scrape_content again on those new links before finally using scrape_provider_search to extract the doctor details.
Repeat Until Success: Repeat this process until you obtain the requested provider information.
Include Links: Include all links in the results for reference.

Search URL Format:
For any provider search task, use the Sonder Health Plans website as your primary source. The search URL should follow this format:
https://sonderhealthplans.com/provider-search-results/page/{page_number}/?directory_type=general&q={search_term}&zip={zip_code}&zip_cityLat&zip_cityLng&in_cat&custom_field%5Bcustom-text-5%5D&custom_field%5Bcustom-select-2%5D&custom_field%5Bcustom-text-4%5D&address&cityLat&cityLng&phone

Adjust the page number and search term as needed. Validate the correct link using the scrape_content function, which can get results from a Google search.

Scraping Instructions:
Use the scrape_provider_search function to scrape provider listings and extract their links from the provided URL.
Use the scrape_content function to retrieve additional links as needed.
If you encounter any difficulties or missing information while scraping, inform the user and offer to try alternative search methods or provide partial results.
Always aim to deliver the most complete and accurate provider information possible using the available links.

Additional Notes:
Use UTF-8 encoding for accessing and writing any CSV or Excel file.
Ensure all columns in the provider table are populated for each provider.
If the initial scrape_provider_search doesn't yield results, use scrape_content to get href results, then use it again on those new hrefs, and finally use scrape_provider_search to get the doctors' details.

Summary of Rules:
Scraping-Related Tasks: Follow the specific scraping instructions and use the functions as directed.
Non-Scraping Tasks: Answer only based on the documents uploaded and ask for user confirmation before any web search.
"""

# Function to handle tool outputs
def handle_tool_outputs(run):
    tool_outputs = []
    try:
        for call in run.required_action.submit_tool_outputs.tool_calls:
            function_name = call.function.name
            function = available_functions.get(function_name)
            if not function:
                raise ValueError(f"Function {function_name} not found in available_functions.")
            arguments = json.loads(call.function.arguments)
            # Use safe_tool_call if necessary
            with st.spinner(f"Executing a detailed search..."):
                output = safe_tool_call(function, function_name, **arguments)

            tool_outputs.append({
                "tool_call_id": call.id,
                "output": json.dumps(output)
            })

        # Use the correct user-specific thread ID here
        return client.beta.threads.runs.submit_tool_outputs(
            thread_id=st.session_state.user_thread.id,
            run_id=run.id,
            tool_outputs=tool_outputs
        )
    except Exception as e:
        st.error(f"Error in handle_tool_outputs: {str(e)}")
        st.error(traceback.format_exc())
        return None


# Function to get agent response
async def get_agent_response(assistant_id, user_message):
    try:
        with st.spinner("Processing your request..."):
            # Use the unique thread for each session
            client.beta.threads.messages.create(
                thread_id=st.session_state.user_thread.id,
                role="user",
                content=user_message,
            )

            run = client.beta.threads.runs.create(
                thread_id=st.session_state.user_thread.id,
                assistant_id=assistant_id,
            )

            while run.status in ["queued", "in_progress"]:
                run = client.beta.threads.runs.retrieve(
                    thread_id=st.session_state.user_thread.id,
                    run_id=run.id
                )
                if run.status == "requires_action":
                    run = handle_tool_outputs(run)
                await asyncio.sleep(1)

            last_message = client.beta.threads.messages.list(thread_id=st.session_state.user_thread.id, limit=1).data[0]

            formatted_response_text = ""
            download_links = []
            images = []

            if last_message.role == "assistant":
                for content in last_message.content:
                    if content.type == "text":
                        formatted_response_text += content.text.value
                        for annotation in content.text.annotations:
                            if annotation.type == "file_path":
                                file_id = annotation.file_path.file_id
                                file_name = annotation.text.split('/')[-1]
                                file_content = client.files.content(file_id).read()
                                download_links.append((file_name, file_content))
                    elif content.type == "image_file":
                        file_id = content.image_file.file_id
                        image_data = client.files.content(file_id).read()
                        images.append((f"{file_id}.png", image_data))
                        formatted_response_text += f"[Image generated: {file_id}.png]\n"
            else:
                formatted_response_text = "Error: No assistant response"

            return formatted_response_text, download_links, images
    except Exception as e:
        st.error(f"Error in get_agent_response: {str(e)}")
        st.error(traceback.format_exc())
        return f"Error: {str(e)}", [], []

# Streamlit app
def main():
    st.set_page_config(page_title="Axxess AI", layout="wide")
    st.title("Axxess AI")

    # Sidebar
    st.sidebar.title("Assistant Configuration")
    st.sidebar.write("The assistant will be created automatically on first run and reused thereafter.")

    # Get or create assistant
    assistant_id = get_or_create_assistant()
    # Apply custom styling
    st.markdown("""
        <style>
        /* ==============================
           General Styling for Consistency
           ============================== */
    
        /* Override default Streamlit text area styling for better compatibility with both light and dark themes */
        .stTextArea textarea {
            background-color: rgba(240, 240, 240, 0.9); /* Light gray background for visibility in both themes */
            color: #333333; /* Dark gray text for good readability on both themes */
            border: 1px solid #dcdcdc; /* Soft border for subtle separation */
            border-radius: 10px; /* Rounded edges for a clean look */
        }
    
        /* ==============================
           User and Assistant Message Styling
           ============================== */
    
        /* Style for user messages to maintain clear distinction */
        .user-message {
            background-color: rgba(240, 240, 240, 0.9); /* Light gray background suitable for both themes */
            color: #333333; /* Dark gray text ensures readability on light backgrounds */
            border: 1px solid #dcdcdc; /* Subtle border for user message clarity */
            border-radius: 10px; /* Rounded corners for smooth appearance */
            padding: 10px; /* Adequate spacing for text */
            margin-bottom: 10px; /* Space between messages */
        }
    
        /* Style for assistant messages for visual contrast */
        .assistant-message {
            background-color: rgba(220, 240, 255, 0.9); /* Light blue background for contrast */
            color: #333333; /* Dark gray text for readability on light backgrounds */
            border: 1px solid #91d5ff; /* Blue border for assistant message clarity */
            border-radius: 10px; /* Rounded edges to match design consistency */
            padding: 10px; /* Uniform spacing for assistant message content */
            margin-bottom: 10px; /* Space between messages */
        }
    
        /* ==============================
           Additional UI Element Styling
           ============================== */
    
        /* Style for the message input area to blend with theme */
        .message-input {
            background-color: rgba(240, 240, 240, 0.9); /* Light gray to blend seamlessly */
            color: #333333; /* Ensures text color is readable */
            border: 1px solid #dcdcdc; /* Soft border to fit the overall design */
            border-radius: 10px; /* Rounded corners for consistency */
            padding: 10px; /* Comfortable spacing inside the input area */
        }
    
        /* Center the send button for a better user experience */
        .send-button {
            display: flex; /* Flexbox for centering */
            justify-content: center; /* Center alignment of the button */
            margin-top: 10px; /* Space above the button */
        }
    
        /* Ensures text input areas are responsive */
        .stTextInput, .stTextArea {
            width: 100% !important; /* Full width for better responsiveness */
        }
    
        /* Style adjustments for Streamlit buttons */
        .stButton button {
            width: 100%; /* Full-width button for a cleaner layout */
            background-color: #007BFF; /* Blue color for consistency with assistant messages */
            color: white; /* White text for button readability */
            border: none; /* Remove default border for a modern look */
            border-radius: 8px; /* Rounded corners for smoother appearance */
            padding: 10px; /* Adequate padding for a larger click area */
            cursor: pointer; /* Pointer cursor to indicate action */
        }
    
        .stButton button:hover {
            background-color: #0056b3; /* Darker blue on hover for better interaction feedback */
        }
        </style>
        """, unsafe_allow_html=True)



    # Chat interface
    if 'messages' not in st.session_state:
        st.session_state.messages = []

    # Display previous messages
    for message in st.session_state.messages:
        with st.container():
            if message["role"] == "user":
                st.markdown(f"""
                    <div class="user-message">
                        <strong>You:</strong> {message['content']}
                    </div>
                    """, unsafe_allow_html=True)
            else:
                st.markdown(f"""
                    <div class="assistant-message">
                        <strong>Assistant:</strong> {message['content']}
                    </div>
                    """, unsafe_allow_html=True)
                if "downloads" in message:
                    for file_name, file_content in message["downloads"]:
                        st.download_button(
                            label=f"Download {file_name} click here",
                            data=file_content,
                            file_name=file_name,
                            mime="application/octet-stream"
                        )
                if "images" in message:
                    for image_name, image_data in message["images"]:
                        st.image(image_data, caption=image_name)
                        st.download_button(
                            label=f"Download {image_name}",
                            data=image_data,
                            file_name=image_name,
                            mime="image/png"
                        )

    # Message input area
    st.markdown('<div class="message-input">', unsafe_allow_html=True)
    prompt = st.text_area("Enter your message here:", height=150)
    st.markdown('</div>', unsafe_allow_html=True)

    # Send button centered
    st.markdown('<div class="send-button">', unsafe_allow_html=True)
    if st.button("Send"):
        if prompt:
            st.session_state.messages.append({"role": "user", "content": prompt})
            with st.spinner("Assistant is typing..."):
                response, download_links, images = asyncio.run(get_agent_response(assistant_id, prompt))
            st.session_state.messages.append({
                "role": "assistant",
                "content": response,
                "downloads": download_links,
                "images": images
            })
            st.rerun()
        else:
            st.warning("Please enter a message.")
    st.markdown('</div>', unsafe_allow_html=True)

if __name__ == "__main__":
    main()
