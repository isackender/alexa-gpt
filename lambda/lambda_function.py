from ask_sdk_core.dispatch_components import AbstractExceptionHandler
from ask_sdk_core.dispatch_components import AbstractRequestHandler
from ask_sdk_core.skill_builder import SkillBuilder
from ask_sdk_core.handler_input import HandlerInput
from ask_sdk_model import Response
import ask_sdk_core.utils as ask_utils
import requests
import logging
import json
import re

# Set your OpenAI API key
api_key = "YOUR_API_KEY"

model = "gpt-4o-mini"

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

class LaunchRequestHandler(AbstractRequestHandler):
    """Handler for Skill Launch."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool

        return ask_utils.is_request_type("LaunchRequest")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "Modo Chat G.P.T. activado"

        session_attr = handler_input.attributes_manager.session_attributes
        session_attr["chat_history"] = []

        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask(speak_output)
                .response
        )

class GptQueryIntentHandler(AbstractRequestHandler):
    """Handler for Gpt Query Intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("GptQueryIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        query = handler_input.request_envelope.request.intent.slots["query"].value

        session_attr = handler_input.attributes_manager.session_attributes
        if "chat_history" not in session_attr:
            session_attr["chat_history"] = []
            session_attr["last_context"] = None
        
        # Process the query to determine if it's a follow-up question
        processed_query, is_followup = process_followup_question(query, session_attr.get("last_context"))
        
        # Generate response with enhanced context handling
        response_data = generate_gpt_response(session_attr["chat_history"], processed_query, is_followup)
        
        # Handle the response data which could be a tuple or string
        if isinstance(response_data, tuple) and len(response_data) == 2:
            response_text, followup_questions = response_data
        else:
            # Fallback for error cases
            response_text = str(response_data)
            followup_questions = []
        
        # Store follow-up questions in the session
        session_attr["followup_questions"] = followup_questions
        
        # Update the conversation history with just the response text, not the questions
        session_attr["chat_history"].append((query, response_text))
        session_attr["last_context"] = extract_context(query, response_text)
        
        # Format the response with follow-up suggestions if available
        response = response_text
        if followup_questions and len(followup_questions) > 0:
            # Add a short pause before the suggestions
            response += " <break time=\"0.5s\"/> "
            response += "Puedes preguntar: "
            # Join with 'or' for the last question
            if len(followup_questions) > 1:
                response += ", ".join([f"'{q}'" for q in followup_questions[:-1]])
                response += f", o '{followup_questions[-1]}'"
            else:
                response += f"'{followup_questions[0]}'"
            response += ". <break time=\"0.5s\"/> Qué te gustaría saber?"
        
        # Prepare response with reprompt that includes the follow-up questions
        reprompt_text = "Puedes hacerme otra pregunta o decir PARA si quieres terminar la conversación."
        if 'followup_questions' in session_attr and session_attr['followup_questions']:
            reprompt_text = "Puedes hacerme otra pregunta, decir 'siguiente' para oír más sugerencias, o decir PARA si quieres terminar la conversación."
        
        return (
            handler_input.response_builder
                .speak(response)
                .ask(reprompt_text)
                .response
        )

class CatchAllExceptionHandler(AbstractExceptionHandler):
    """Generic error handling to capture any syntax or routing errors."""
    def can_handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> bool
        return True

    def handle(self, handler_input, exception):
        # type: (HandlerInput, Exception) -> Response
        logger.error(exception, exc_info=True)

        speak_output = "Lo siento, tuve un problema al procesar tu petición. Inténtalo de nuevo."

        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask(speak_output)
                .response
        )

class CancelOrStopIntentHandler(AbstractRequestHandler):
    """Single handler for Cancel and Stop Intent."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return (ask_utils.is_intent_name("AMAZON.CancelIntent")(handler_input) or
                ask_utils.is_intent_name("AMAZON.StopIntent")(handler_input))

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        speak_output = "Saliendo del modo Chat G.P.T."

        return (
            handler_input.response_builder
                .speak(speak_output)
                .response
        )

def process_followup_question(question, last_context):
    """Processes a question to determine if it's a follow-up and enhances it with context if needed"""
    # Common follow-up indicators
    followup_patterns = [
        r'^(¿?y|pero|entonces|además|también)\s',  # typical conectors
        r'^(¿?qué|cómo|por qué|cuándo|dónde|quién|cuál)\s+(es|son|fue|fueron|hace|hizo|puede|podría|hará|debo|debería|hay)\s',
        r'^(¿?puedes|podrías|debes|deberías|puedo|podría|quieres|querrías|vamos|vamos a)\s',
        r'^(¿?es|está|están|eran|eran|fue|fueron|hay|hubo|hubiera)\s+(eso|esto|aquello|ellos|ellas|él|ella)\s',
        r'^(cuéntame más|explícame mejor|amplía eso|dime más)\s*',
        r'^(¿?por qué|cómo)\?*$'  # simple questions
    ]
    
    is_followup = False
    
    # Check if the question matches any follow-up patterns
    for pattern in followup_patterns:
        if re.search(pattern, question.lower()):
            is_followup = True
            break
    
    # If it's a follow-up and we have context, we don't need to modify the question
    # The context will be handled in the generate_gpt_response function
    return question, is_followup

def extract_context(question, response):
    """Extracts the main context from a Q&A pair for future reference"""
    # This is a simple implementation that just returns the question and response
    # In a more advanced implementation, you could use NLP to extract key entities
    return {"question": question, "response": response}

def generate_followup_questions(conversation_context, query, response, count=2):
    """Generates concise follow-up questions based on the conversation context"""
    try:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        url = "https://api.openai.com/v1/chat/completions"
        
        # Prepare a focused prompt for brief follow-ups
        messages = [
            {"role": "system", "content": "Eres un asistente que propone preguntas cortas como sugerencia. Responde siempre en español de España"},
            {"role": "user", "content": """Basándote en la conversación, sugiere 2 preguntas muy cortas como continuación (máximo 5 palabras cada una). 
            Hazlas directas y simples. Devuelve ÚNICAMENTE las preguntas separadas por '|'.
            Ejemplo: Cuál es la capital?|Cómo de grande es?"""}
        ]
        
        # Add conversation context
        if conversation_context:
            last_q, last_a = conversation_context[-1]
            messages.append({"role": "user", "content": f"Pregunta anterior: {last_q}"})
            messages.append({"role": "assistant", "content": last_a})
        
        messages.append({"role": "user", "content": f"Pregunta actual: {query}"})
        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user", "content": "Preguntas de continuación (separadas por |):"})
        
        data = {
            "model": "gpt-3.5-turbo",  # Using a faster model for this
            "messages": messages,
            "max_tokens": 50,
            "temperature": 0.7
        }
        
        response = requests.post(url, headers=headers, data=json.dumps(data), timeout=3)
        if response.ok:
            questions_text = response.json()['choices'][0]['message']['content'].strip()
            # Clean and split the response
            questions = [q.strip().rstrip('?') for q in questions_text.split('|') if q.strip()]
            # Ensure we have valid questions
            questions = [q for q in questions if len(q.split()) <= 4 and len(q) > 0][:2]
            
            # If we don't have enough questions, provide defaults
            if len(questions) < 2:
                questions = ["Cuéntame más", "Dame un ejemplo"]
                
            logger.info(f"Generated follow-up questions: {questions}")
            return questions
            
        logger.error(f"API Error: {response.text}")
        return ["Cuéntame más", "Dame un ejemplo"]
        
    except Exception as e:
        logger.error(f"Error in generate_followup_questions: {str(e)}")
        return ["Cuéntame más", "Dame un ejemplo"]

def generate_gpt_response(chat_history, new_question, is_followup=False):
    """Generates a GPT response to a question with enhanced context handling"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    url = "https://api.openai.com/v1/chat/completions"
    
    # Create a more informative system message based on whether this is a follow-up
    system_message = "Eres un asistente servicial. Responde en 50 palabras o menos."
    if is_followup:
        system_message += " Esto es una pregunta de continuación con la conversación anterior. Mantén el contexto sin repetir información que ya se haya dado."
    
    messages = [{"role": "system", "content": system_message}]
    
    # Include relevant conversation history
    # For follow-ups, we include more context. For new questions, we limit to save tokens
    history_limit = 10 if not is_followup else 5
    for question, answer in chat_history[-history_limit:]:
        messages.append({"role": "user", "content": question})
        messages.append({"role": "assistant", "content": answer})
    
    # Add the new question
    messages.append({"role": "user", "content": new_question})
    
    data = {
        "model": model,
        "messages": messages,
        "max_tokens": 300
    }
    
    try:
        response = requests.post(url, headers=headers, data=json.dumps(data))
        response_data = response.json()
        if response.ok:
            response_text = response_data['choices'][0]['message']['content']
            
            # Generate follow-up questions for the response
            try:
                # Always try to generate follow-up questions
                followup_questions = generate_followup_questions(
                    chat_history + [(new_question, response_text)], 
                    new_question, 
                    response_text
                )
                logger.info(f"Generated follow-up questions: {followup_questions}")
            except Exception as e:
                logger.error(f"Error generating follow-up questions: {str(e)}")
                followup_questions = []
            
            return response_text, followup_questions
        else:
            return f"Error {response.status_code}: {response_data['error']['message']}", []
    except Exception as e:
        logger.error(f"Error generating response: {str(e)}")
        return f"Error generating response: {str(e)}", []

class ClearContextIntentHandler(AbstractRequestHandler):
    """Handler for clearing conversation context."""
    def can_handle(self, handler_input):
        # type: (HandlerInput) -> bool
        return ask_utils.is_intent_name("ClearContextIntent")(handler_input)

    def handle(self, handler_input):
        # type: (HandlerInput) -> Response
        session_attr = handler_input.attributes_manager.session_attributes
        session_attr["chat_history"] = []
        session_attr["last_context"] = None
        
        speak_output = "He limpiado el historial de conversaciones. Sobre qué te gustaría hablar?"
        
        return (
            handler_input.response_builder
                .speak(speak_output)
                .ask(speak_output)
                .response
        )

sb = SkillBuilder()

sb.add_request_handler(LaunchRequestHandler())
sb.add_request_handler(GptQueryIntentHandler())
sb.add_request_handler(ClearContextIntentHandler())
sb.add_request_handler(CancelOrStopIntentHandler())
sb.add_exception_handler(CatchAllExceptionHandler())

lambda_handler = sb.lambda_handler()
