#
#
# def generate_prompt(curr_input, prompt_template):
#   """
#   Takes in the current input (e.g. comment that you want to classifiy) and
#   the path to a prompt file. The prompt file contains the raw str prompt that
#   will be used, which contains the following substr: !<INPUT>! -- this
#   function replaces this substr with the actual curr_input to produce the
#   final prompt that will be sent to the GPT3 server.
#   ARGS:
#     curr_input: the input we want to feed in (IF THERE ARE MORE THAN ONE
#                 INPUT, THIS CAN BE A LIST.)
#     prompt_template: the prompt template content.
#   RETURNS:
#     a str prompt that will be sent to OpenAI's GPT server.
#   """
#   if type(curr_input) == type("string"):
#     curr_input = [curr_input]
#   curr_input = [str(i) for i in curr_input]
#
#   prompt_result = prompt_template
#   for count, i in enumerate(curr_input):
#     prompt_result = prompt_result.replace(f"!<INPUT {count}>!", i)
#   if "<commentblockmarker>###</commentblockmarker>" in prompt_template:
#     prompt_result = prompt_result.split("<commentblockmarker>###</commentblockmarker>")[1]
#   return prompt_result.strip()