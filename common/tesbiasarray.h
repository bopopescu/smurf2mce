#include <stdexcept>
#include <mutex>
//#include "smurf2mce.h"  // defines all the SMURF stuff

#ifndef __TES_BIAS_ARRAY_H__
#define __TES_BIAS_ARRAY_H__

static const std::size_t TesBiasCount = 16;  // 16 Tes Bias values
static const std::size_t TesBiasBufferSize = TesBiasCount * 20 / 8; // 16x 20-bit bytes

// Class to handle TES bias array of values.
class TesBiasArray
{
private:
    // Helper class to handler indexes of TES bias words
    // TES bias are 20-bit words = 2.5 bytes
    // 16x TES bias occupy 40 bytes, which are divided into 8 blocks of 2 words (5 bytes) each
    // From each word index (0-15) a block number (0-7), and a word sub-index inside that block (0-1)
    // is generated. For example, TES bias 6 and 7 are both located on block 3; with 6 at the first word
    // and 7 on the second word inside that block.
    class WordIndex
    {
    public:
        WordIndex(std::size_t i) : index( i ), block( i / 2 ), word( i % 2 ) {};

        std::size_t Index() const { return index; }; // 20-bit word index
        std::size_t Block() const { return block; }; // 2-word block number
        std::size_t Word()  const { return word;  }; // Word index inside the block

        bool operator >= (std::size_t rhs) const { return index >= rhs; };

    private:
        std::size_t index; // 20-bit word index (0-15)
        std::size_t block; // 2-word block index (0-7)
        std::size_t word;  // Word index in the word block (0-1)
    };

    // Helper Union to access individual bytes of a word
    typedef union
    {
      unsigned int word;
      uint8_t byte[4];
    } U;

    // Helper Struct to sign extend 20-bit values
    typedef struct
    {
      signed int word:20;
    } S;

    // Pointer to the data buffer
    uint8_t *pData;

    // Max buffer size (in 20-bit words)
    std::size_t bufferSize;

    // Mutex, to safetly access the data from different threads
    std::mutex mut;

public:
   TesBiasArray(uint8_t *p, std::size_t size) : pData(p), bufferSize(size) {};
    ~TesBiasArray() {};

    // Change the data pointer
    void setPtr(uint8_t *p) { pData = p; };

    // Write a TES bias value
    void setWord(const WordIndex& index, int value) const
    {
        if (index >= bufferSize)
            throw std::runtime_error("Trying to write a TES bias value in an address of out the buffer range.");

        if (index.Word() == 0)
        {
            // Create an union pointing to the block
            uint8_t *b = (pData + 5*index.Block());

            // Create an union with the passed value
            U v { value };
            *b++ = v.byte[0];
            *b++ = v.byte[1];
            uint8_t temp = *b;
            temp &= 0xf0;
            temp |= (v.byte[2] & 0x0f);
            *b = temp;
        }
        else
        {
            // Create an union pointing to the block
            uint8_t *b = (pData + 5*index.Block() + 2);

            // Create an union with the passed value
            U v { value << 4 };
            uint8_t temp = *b;
            temp &= 0x0f;
            temp |= (v.byte[0] & 0xf0);
            *b++ = temp;
            *b++ = v.byte[1];
            *b = v.byte[2];
        }
    };

    // Read a TES bias value
    int getWord(const WordIndex& index) const
    {
        if (index >= bufferSize)
            throw std::runtime_error("Trying to read a TES bias value in an address of out the buffer range.");

        std::size_t offset(0), shift(0);

        if (index.Word())
        {
           offset = 2;
           shift = 4;
        }

        return S { static_cast<int>( *( reinterpret_cast<uint32_t*>( pData + 5*index.Block() + offset ) ) >> shift )  }.word;
    }

    // Method to the mutex
    std::mutex* getMutex() { return &mut; };
};

#endif